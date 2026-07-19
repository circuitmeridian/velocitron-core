"""ANTLR lowering and v1 Contribution IR resolution for the Coin Deposit slice."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any, cast

from antlr4 import CommonTokenStream, InputStream, Token
from antlr4.Recognizer import Recognizer
from antlr4.error.ErrorListener import ErrorListener
from antlr4.error.Errors import RecognitionException
from velocitron.cel import get_default_adapter
from velocitron.parser import NetValidationError, parse_composition, parse_net
from .diagnostics import (
    Diagnostic,
    PetrinetDslError,
    RelatedDiagnostic,
    SourcePosition,
    SourceSpan,
)
from .generated.VelocitronPetriNetLexer import VelocitronPetriNetLexer
from .generated.VelocitronPetriNetParser import VelocitronPetriNetParser
from .generated.VelocitronPetriNetVisitor import VelocitronPetriNetVisitor

_FORMAT = "velocitron.petrinet/contribution-ir"
_VERSION = 1

_JSON_NUMBER = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$")
_RELATIVE_SOURCE_ID = re.compile(
    r"^(?!/)(?![A-Za-z]:[\\/])(?![A-Za-z][A-Za-z0-9+.-]*:).+$"
)
_TIMER_BIND_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class SourceInput:
    """Text and explicit portable identity for one DSL source."""

    source_id: str
    text: str


class _SourceMap:
    def __init__(self, source_id: str, text: str) -> None:
        self.source_id = source_id
        self.text = text
        self._line_starts = [0]
        for index, character in enumerate(text):
            if character == "\n":
                self._line_starts.append(index + 1)

    def position(self, index: int) -> SourcePosition:
        index = max(0, min(index, len(self.text)))
        line_index = 0
        for candidate, start in enumerate(self._line_starts):
            if start > index:
                break
            line_index = candidate
        start = self._line_starts[line_index]
        return SourcePosition(
            byte_offset=len(self.text[:index].encode("utf-8")),
            line=line_index + 1,
            column=index - start + 1,
        )

    def span(self, start: int, end: int) -> SourceSpan:
        return SourceSpan(self.source_id, self.position(start), self.position(end))


class _SyntaxListener(ErrorListener):
    def __init__(self, source: _SourceMap) -> None:
        self.source = source
        self.diagnostics: list[Diagnostic] = []

    def syntaxError(
        self,
        recognizer: Recognizer,
        offendingSymbol: Token | None,
        line: int,
        column: int,
        msg: str,
        e: RecognitionException | None,
    ) -> None:
        del recognizer, line, column, e
        start = (
            len(self.source.text) if offendingSymbol is None else offendingSymbol.start
        )
        end = start if offendingSymbol is None else max(start, offendingSymbol.stop + 1)
        if offendingSymbol is not None and offendingSymbol.type == Token.EOF:
            start = end = len(self.source.text)
        help_text: str | None = None
        if "expecting IDENT" in msg or msg.startswith("missing IDENT at "):
            if msg.startswith("missing IDENT at ") and offendingSymbol is not None:
                msg = f"mismatched input {offendingSymbol.text!r} expecting identifier"
            else:
                msg = msg.replace("expecting IDENT", "expecting identifier")
            help_text = "add a mandatory identifier alias after as"
        self.diagnostics.append(
            Diagnostic("PN101", msg, self.source.span(start, end), help_text)
        )


def _contains_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _valid_capacity_key(value: object) -> bool:
    if isinstance(value, str):
        return bool(value)
    if not isinstance(value, list) or not value:
        return False
    return all(
        isinstance(item, str) and bool(item) for item in cast(list[object], value)
    )


def _decode_name(text: str) -> str:
    return json.loads(text) if text.startswith('"') else text


def _tag(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    if isinstance(value, (int, float)):
        return {"type": "number", "lexeme": str(value).lower()}
    if isinstance(value, list):
        items = cast(list[Any], value)
        return {"type": "array", "items": [_tag(item) for item in items]}
    if isinstance(value, dict):
        entries = cast(dict[str, Any], value)
        return {
            "type": "object",
            "entries": [
                {"key": key, "value": _tag(item)} for key, item in entries.items()
            ],
        }
    raise TypeError(f"not a JSON value: {value!r}")


def _untag(value: Mapping[str, Any]) -> Any:
    kind = value.get("type")
    if kind == "null":
        if set(value) != {"type"}:
            raise ValueError("null has unexpected members")
        return None
    if kind == "boolean":
        if set(value) != {"type", "value"} or not isinstance(value["value"], bool):
            raise ValueError("invalid boolean")
        return value["value"]
    if kind == "string":
        if set(value) != {"type", "value"} or not isinstance(value["value"], str):
            raise ValueError("invalid string")
        if _contains_surrogate(value["value"]):
            raise ValueError("string contains an isolated surrogate")
        return value["value"]
    if kind == "number":
        lexeme = value.get("lexeme")
        if (
            set(value) != {"type", "lexeme"}
            or not isinstance(lexeme, str)
            or _JSON_NUMBER.fullmatch(lexeme) is None
        ):
            raise ValueError("invalid number")
        parsed = float(lexeme)
        if not math.isfinite(parsed):
            raise ValueError("number must be finite")
        if (
            all(marker not in lexeme for marker in ".eE")
            and abs(int(lexeme)) > 9007199254740991
        ):
            raise ValueError("integer exceeds the safe IEEE-754 range")
        return int(lexeme) if all(marker not in lexeme for marker in ".eE") else parsed
    if kind == "array":
        items = value.get("items")
        if set(value) != {"type", "items"} or not isinstance(items, list):
            raise ValueError("invalid array")
        typed_items = cast(list[Any], items)
        if not all(isinstance(item, Mapping) for item in typed_items):
            raise ValueError("invalid array")
        return [_untag(cast(Mapping[str, Any], item)) for item in typed_items]
    if kind == "object":
        entries = value.get("entries")
        if set(value) != {"type", "entries"} or not isinstance(entries, list):
            raise ValueError("invalid object")
        result: dict[str, Any] = {}
        for raw_entry in cast(list[Any], entries):
            if not isinstance(raw_entry, Mapping):
                raise ValueError("invalid object entry")
            entry = cast(Mapping[str, Any], raw_entry)
            if set(entry) != {"key", "value"}:
                raise ValueError("invalid object entry")
            key = entry["key"]
            raw_value = entry["value"]
            if (
                not isinstance(key, str)
                or key in result
                or not isinstance(raw_value, Mapping)
            ):
                raise ValueError("invalid or duplicate object key")
            result[key] = _untag(cast(Mapping[str, Any], raw_value))
        return result
    raise ValueError("unknown tagged JSON value")


def json_values_equal(left: Any, right: Any) -> bool:
    """Compare decoded JSON values using JSON, rather than Python, scalar types."""
    left_is_number = isinstance(left, (int, float)) and not isinstance(left, bool)
    right_is_number = isinstance(right, (int, float)) and not isinstance(right, bool)
    if left_is_number or right_is_number:
        return left_is_number and right_is_number and bool(left == right)
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        typed_left = cast(list[Any], left)
        typed_right = cast(list[Any], right)
        return len(typed_left) == len(typed_right) and all(
            json_values_equal(left_item, right_item)
            for left_item, right_item in zip(typed_left, typed_right, strict=True)
        )
    if isinstance(left, dict):
        typed_left = cast(dict[str, Any], left)
        typed_right = cast(dict[str, Any], right)
        return len(typed_left) == len(typed_right) and all(
            key in typed_right and json_values_equal(value, typed_right[key])
            for key, value in typed_left.items()
        )
    return bool(left == right)


class _LoweringVisitor(VelocitronPetriNetVisitor):
    def __init__(self, source: _SourceMap) -> None:
        super().__init__()
        self.source = source
        self.contributions: list[dict[str, Any]] = []
        self.document_kind: str | None = None
        self._statement = 0

    def _json_error(self, message: str, ctx: Any) -> None:
        raise PetrinetDslError(
            Diagnostic(
                "PN101",
                message,
                self.source.span(ctx.start.start, ctx.stop.stop + 1),
            )
        )

    def _semantic_error(self, message: str, ctx: Any) -> None:
        raise PetrinetDslError(
            Diagnostic(
                "PN202",
                message,
                self.source.span(ctx.start.start, ctx.stop.stop + 1),
            )
        )

    def _lower_json_value(self, ctx: Any) -> dict[str, Any]:
        if ctx.jsonObject() is not None:
            return self._lower_json_object(ctx.jsonObject())
        if ctx.jsonArray() is not None:
            return {
                "type": "array",
                "items": [
                    self._lower_json_value(item) for item in ctx.jsonArray().jsonValue()
                ],
            }
        string = ctx.STRING()
        if string is not None:
            value = json.loads(string.getText())
            if _contains_surrogate(value):
                self._json_error("JSON string contains an isolated surrogate", ctx)
            return {"type": "string", "value": value}
        number = ctx.NUMBER()
        if number is None and hasattr(ctx, "POSITIVE_INTEGER"):
            number = ctx.POSITIVE_INTEGER()
        if number is None and hasattr(ctx, "ZERO"):
            number = ctx.ZERO()
        if number is not None:
            lexeme = number.getText()
            parsed = float(lexeme)
            if not math.isfinite(parsed):
                self._json_error("JSON number must be finite IEEE-754 binary64", ctx)
            if (
                all(marker not in lexeme for marker in ".eE")
                and abs(int(lexeme)) > 9007199254740991
            ):
                self._json_error("JSON integer exceeds the safe IEEE-754 range", ctx)
            return {"type": "number", "lexeme": lexeme}
        if ctx.TRUE() is not None:
            return {"type": "boolean", "value": True}
        if ctx.FALSE() is not None:
            return {"type": "boolean", "value": False}
        return {"type": "null"}

    def _lower_json_object(self, ctx: Any) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        keys: set[str] = set()
        for key_token, value_ctx in zip(ctx.STRING(), ctx.jsonValue(), strict=True):
            key = json.loads(key_token.getText())
            if _contains_surrogate(key):
                self._json_error("JSON object key contains an isolated surrogate", ctx)
            if key in keys:
                self._json_error(f"duplicate JSON object key {key!r}", ctx)
            keys.add(key)
            entries.append({"key": key, "value": self._lower_json_value(value_ctx)})
        return {"type": "object", "entries": entries}

    def _span(self, start: int, end: int) -> dict[str, Any]:
        return self.source.span(start, end).as_dict()

    def _contribution(
        self,
        *,
        kind: str,
        target: dict[str, Any],
        value: dict[str, Any],
        start: int,
        end: int,
        part: int = 0,
    ) -> None:
        statement = self._statement
        self.contributions.append(
            {
                "id": {
                    "source": self.source.source_id,
                    "statement": statement,
                    "part": part,
                },
                "kind": kind,
                "ordinal": len(self.contributions),
                "span": self._span(start, end),
                "target": target,
                "value": value,
            }
        )

    def visitDocument(self, ctx: Any) -> None:
        wrapper_accessors = {
            VelocitronPetriNetParser.AdditionalChainContext: "chain",
            VelocitronPetriNetParser.AdditionalTransitionHandlerContext: (
                "transitionHandler"
            ),
            VelocitronPetriNetParser.AdditionalInitialMarkingContext: (
                "initialMarking"
            ),
            VelocitronPetriNetParser.AdditionalTemplateDefinitionContext: (
                "templateDefinition"
            ),
        }
        statement_types = (
            VelocitronPetriNetParser.NetHeaderContext,
            VelocitronPetriNetParser.CompositionHeaderContext,
            VelocitronPetriNetParser.CompositionUseContext,
            VelocitronPetriNetParser.CompositionWireContext,
            VelocitronPetriNetParser.ChainContext,
            VelocitronPetriNetParser.PlaceDeclarationContext,
            VelocitronPetriNetParser.TransitionDeclarationContext,
            VelocitronPetriNetParser.TransitionHandlerContext,
            VelocitronPetriNetParser.TransitionGuardContext,
            VelocitronPetriNetParser.TransitionTimerContext,
            VelocitronPetriNetParser.TransitionTimerMaturityContext,
            VelocitronPetriNetParser.TransitionTimerBindContext,
            VelocitronPetriNetParser.TransitionPriorityContext,
            VelocitronPetriNetParser.TransitionOrderContext,
            VelocitronPetriNetParser.ChainOrderContext,
            VelocitronPetriNetParser.PlacePortContext,
            VelocitronPetriNetParser.PlaceAcceptsContext,
            VelocitronPetriNetParser.PlaceCapacityContext,
            VelocitronPetriNetParser.ArcWeightContext,
            VelocitronPetriNetParser.ArcDataContext,
            VelocitronPetriNetParser.ArcPredicateContext,
            VelocitronPetriNetParser.ArcCorrelateContext,
            VelocitronPetriNetParser.InitialMarkingContext,
            VelocitronPetriNetParser.TemplateDefinitionContext,
            VelocitronPetriNetParser.NamedMarkingContext,
            VelocitronPetriNetParser.MetadataDescriptionContext,
            VelocitronPetriNetParser.MetadataAnnotationContext,
            VelocitronPetriNetParser.ViewPositionContext,
            VelocitronPetriNetParser.ViewRouteContext,
            VelocitronPetriNetParser.ExtensionsContext,
        )
        for child in ctx.children:
            accessor = next(
                (
                    name
                    for wrapper_type, name in wrapper_accessors.items()
                    if isinstance(child, wrapper_type)
                ),
                None,
            )
            if accessor is not None:
                child = getattr(child, accessor)()
            if isinstance(child, statement_types):
                self.visit(child)
                self._statement += 1

    def visitNetHeader(self, ctx: Any) -> None:
        description = ctx.STRING()
        self._contribution(
            kind="document.net-header",
            target={"type": "document"},
            value={
                "name": _decode_name(ctx.name().getText()),
                **(
                    {"description": json.loads(description.getText())}
                    if description
                    else {}
                ),
            },
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitCompositionHeader(self, ctx: Any) -> None:
        self.document_kind = "composition"
        self._contribution(
            kind="document.composition-header",
            target={"type": "document"},
            value={"namespace": _decode_name(ctx.name().getText())},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitCompositionUse(self, ctx: Any) -> None:
        self._contribution(
            kind="composition.use",
            target={"type": "document"},
            value={
                "ref": json.loads(ctx.STRING().getText()),
                "alias": ctx.IDENT().getText(),
            },
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitCompositionWire(self, ctx: Any) -> None:
        aliases = ctx.IDENT()
        places = ctx.place()
        self._contribution(
            kind="composition.wire",
            target={"type": "document"},
            value={
                "from": {
                    "alias": aliases[0].getText(),
                    "place": _decode_name(places[0].name().getText()),
                    "span": self._span(
                        aliases[0].getSymbol().start, places[0].stop.stop + 1
                    ),
                },
                "to": {
                    "alias": aliases[1].getText(),
                    "place": _decode_name(places[1].name().getText()),
                    "span": self._span(
                        aliases[1].getSymbol().start, places[1].stop.stop + 1
                    ),
                },
            },
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitChain(self, ctx: Any) -> None:
        nodes = ctx.chainNode()
        segments = ctx.arcSegment()
        handle_ctx = ctx.chainHandle()
        arc_specs: list[
            tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, int, int, Any]
        ] = []
        for index, segment in enumerate(segments):
            left = nodes[index]
            right = nodes[index + 1]
            left_place = left.place()
            right_place = right.place()
            if (left_place is None) == (right_place is None):
                raise PetrinetDslError(
                    Diagnostic(
                        "PN101",
                        "a chain segment must connect a place and a transition",
                        self.source.span(left.start.start, right.stop.stop + 1),
                    )
                )
            operator = segment.arcOperator().getText()
            color_ctx = segment.color()
            if color_ctx is None:
                color = {"kind": "explicit", "value": "token"}
            else:
                color = {
                    "kind": "explicit",
                    "value": _decode_name(color_ctx.name().getText()),
                }
            if left_place is not None:
                mode = {
                    "->": "consume",
                    "->?": "read",
                    "->0": "inhibit",
                }.get(operator)
                if mode is None:
                    self._json_error("invalid input arc operator", segment)
                assert mode is not None
                source = {
                    "type": "place",
                    "name": _decode_name(left_place.name().getText()),
                }
                destination = {
                    "type": "transition",
                    "name": _decode_name(right.transition().name().getText()),
                }
                transition_ctx = right.transition()
            else:
                if operator != "->":
                    arc_operator = segment.arcOperator()
                    raise PetrinetDslError(
                        Diagnostic(
                            "PN101",
                            f"{operator} is only allowed on place-to-transition arcs",
                            self.source.span(
                                arc_operator.start.start,
                                arc_operator.stop.stop + 1,
                            ),
                        )
                    )
                mode = "produce"
                source = {
                    "type": "transition",
                    "name": _decode_name(left.transition().name().getText()),
                }
                destination = {
                    "type": "place",
                    "name": _decode_name(right_place.name().getText()),
                }
                transition_ctx = None
            arc_specs.append(
                (
                    source,
                    destination,
                    color,
                    mode,
                    left.start.start,
                    right.stop.stop + 1,
                    transition_ctx,
                )
            )
        part_offset = 1 if handle_ctx is not None else 0
        arc_ids = [
            {
                "document": self.source.source_id,
                "statement": self._statement,
                "part": part_offset + index,
            }
            for index in range(len(arc_specs))
        ]
        if handle_ctx is not None:
            handle = _decode_name(handle_ctx.name().getText())
            self._contribution(
                kind="arc.handle",
                target={"type": "arcHandle", "name": handle},
                value={"arcIds": arc_ids},
                start=ctx.start.start,
                end=ctx.stop.stop + 1,
            )
        for index, (
            source,
            destination,
            color,
            mode,
            start,
            end,
            transition_ctx,
        ) in enumerate(arc_specs):
            value: dict[str, Any] = {
                "from": source,
                "to": destination,
                "color": color,
                "mode": mode,
            }
            if mode == "consume":
                value["transitionNameSpan"] = self._span(
                    transition_ctx.name().start.start,
                    transition_ctx.name().stop.stop + 1,
                )
            self._contribution(
                kind="arc.declare",
                target={"type": "arc", "id": arc_ids[index]},
                value=value,
                start=start,
                end=end,
                part=part_offset + index,
            )

    def visitPlaceDeclaration(self, ctx: Any) -> None:
        place = ctx.place()
        self._contribution(
            kind="place.declare",
            target={"type": "place", "name": _decode_name(place.name().getText())},
            value={},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitTransitionDeclaration(self, ctx: Any) -> None:
        transition = ctx.transition()
        self._contribution(
            kind="transition.declare",
            target={
                "type": "transition",
                "name": _decode_name(transition.name().getText()),
            },
            value={},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitPlacePort(self, ctx: Any) -> None:
        self._contribution(
            kind="place.port",
            target={
                "type": "place",
                "name": _decode_name(ctx.place().name().getText()),
            },
            value={
                "direction": ctx.portDirection().getText(),
                "color": _decode_name(ctx.color().name().getText()),
            },
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitTransitionHandler(self, ctx: Any) -> None:
        name = _decode_name(ctx.transition().name().getText())
        self._contribution(
            kind="transition.handler",
            target={"type": "transition", "name": name},
            value={"handler": json.loads(ctx.STRING().getText())},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitTransitionGuard(self, ctx: Any) -> None:
        name = _decode_name(ctx.transition().name().getText())
        self._contribution(
            kind="transition.guard",
            target={"type": "transition", "name": name},
            value={"guard": json.loads(ctx.STRING().getText())},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitTransitionTimer(self, ctx: Any) -> None:
        name = _decode_name(ctx.transition().name().getText())
        clock = _decode_name(ctx.place().name().getText())
        self._contribution(
            kind="transition.timer",
            target={"type": "transition", "name": name},
            value={
                "clock": {"type": "place", "name": clock},
                "cel": json.loads(ctx.STRING().getText()),
            },
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitTransitionTimerMaturity(self, ctx: Any) -> None:
        name = _decode_name(ctx.transition().name().getText())
        self._contribution(
            kind="transition.timer-maturity",
            target={"type": "transition", "name": name},
            value={"maturity": json.loads(ctx.STRING().getText())},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitTransitionTimerBind(self, ctx: Any) -> None:
        name = _decode_name(ctx.transition().name().getText())
        bind_name = ctx.timerBindName().getText()
        place = _decode_name(ctx.place().name().getText())
        self._contribution(
            kind="transition.timer-bind",
            target={"type": "transition", "name": name},
            value={
                "name": bind_name,
                "place": {"type": "place", "name": place},
            },
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def _safe_integer(self, ctx: Any) -> int:
        value = int(ctx.getText())
        if value > 9007199254740991:
            self._json_error("integer exceeds the safe IEEE-754 range", ctx)
        return value

    def visitTransitionPriority(self, ctx: Any) -> None:
        name = _decode_name(ctx.transition().name().getText())
        self._contribution(
            kind="transition.priority",
            target={"type": "transition", "name": name},
            value={"priority": self._safe_integer(ctx.nonnegativeInteger())},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitTransitionOrder(self, ctx: Any) -> None:
        name = _decode_name(ctx.transition().name().getText())
        self._contribution(
            kind="order.transition",
            target={"type": "transition", "name": name},
            value={"rank": self._safe_integer(ctx.positiveInteger())},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitChainOrder(self, ctx: Any) -> None:
        name = _decode_name(ctx.name().getText())
        self._contribution(
            kind="order.arc-run",
            target={"type": "arcHandle", "name": name},
            value={"rank": self._safe_integer(ctx.positiveInteger())},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitPlaceAccepts(self, ctx: Any) -> None:
        place = _decode_name(ctx.place().name().getText())
        colors = [_decode_name(color.getText()) for color in ctx.color()]
        if len(colors) != len(set(colors)):
            self._semantic_error(
                f"accepted colors for ({place}) must not contain duplicates",
                ctx,
            )
        self._contribution(
            kind="place.accepts",
            target={"type": "place", "name": place},
            value={"colors": colors},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitPlaceCapacity(self, ctx: Any) -> None:
        place = _decode_name(ctx.place().name().getText())
        object_ctx = ctx.jsonObject()
        tagged = self._lower_json_object(object_ctx)
        value = cast(dict[str, Any], _untag(tagged))
        value_contexts = {
            json.loads(key.getText()): item
            for key, item in zip(
                object_ctx.STRING(), object_ctx.jsonValue(), strict=True
            )
        }
        if set(value) != {"key", "max"}:
            self._semantic_error(
                f"capacityPerColorKey for ({place}) must contain exactly key and max",
                object_ctx,
            )
        key = value["key"]
        if not _valid_capacity_key(key):
            self._semantic_error(
                "capacityPerColorKey key must be a non-empty string or non-empty "
                f"array of non-empty strings for ({place})",
                value_contexts["key"],
            )
        maximum = value["max"]
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
            self._semantic_error(
                "capacityPerColorKey max must be an integer greater than or equal "
                f"to 1; got {maximum!r} for ({place})",
                value_contexts["max"],
            )
        self._contribution(
            kind="place.capacity-per-color-key",
            target={"type": "place", "name": place},
            value=value,
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitArcWeight(self, ctx: Any) -> None:
        handle = _decode_name(ctx.name().getText())
        value_ctx = ctx.jsonValue()
        tagged = self._lower_json_value(value_ctx)
        weight = _untag(tagged)
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 1:
            self._semantic_error(
                "arc weight must be an integer greater than or equal to 1; "
                f"got {weight!r} for @{handle}",
                value_ctx,
            )
        self._contribution(
            kind="arc.weight",
            target={"type": "arcHandle", "name": handle},
            value={"weight": weight},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitArcData(self, ctx: Any) -> None:
        handle = _decode_name(ctx.name().getText())
        if ctx.CEL() is not None:
            # The computed variant (ADR 0023): `data cel JsonString` lowers
            # to the produce template's `cel` field, not literal data.
            self._contribution(
                kind="arc.produce-cel",
                target={"type": "arcHandle", "name": handle},
                value={"cel": json.loads(ctx.STRING().getText())},
                start=ctx.start.start,
                end=ctx.stop.stop + 1,
            )
            return
        self._contribution(
            kind="arc.produce-data",
            target={"type": "arcHandle", "name": handle},
            value={"data": self._lower_json_value(ctx.jsonValue())},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitArcPredicate(self, ctx: Any) -> None:
        handle = _decode_name(ctx.name().getText())
        kind = ctx.predicateKind().getText()
        self._contribution(
            kind="arc.predicate",
            target={"type": "arcHandle", "name": handle},
            value={"kind": kind, kind: json.loads(ctx.STRING().getText())},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitArcCorrelate(self, ctx: Any) -> None:
        handle = _decode_name(ctx.name().getText())
        self._contribution(
            kind="arc.correlate",
            target={"type": "arcHandle", "name": handle},
            value={"cel": json.loads(ctx.STRING().getText())},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def _lower_marking_value(self, ctx: Any) -> tuple[int, dict[str, Any]]:
        count_ctx = ctx.positiveInteger()
        count = self._safe_integer(count_ctx) if count_ctx is not None else 1
        template_ctx = ctx.templateReference()
        if template_ctx is not None:
            return count, {
                "template": {
                    "type": "template",
                    "name": _decode_name(template_ctx.name().getText()),
                }
            }
        return count, {
            "color": "token",
            "data": {"type": "object", "entries": []},
        }

    def visitInitialMarking(self, ctx: Any) -> None:
        count, token = self._lower_marking_value(ctx.markingValue())
        self._contribution(
            kind="marking.append",
            target={
                "type": "place",
                "name": _decode_name(ctx.place().name().getText()),
            },
            value={"count": count, "token": token},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitTemplateDefinition(self, ctx: Any) -> None:
        template = _decode_name(ctx.templateReference().name().getText())
        value = self._lower_json_value(ctx.jsonValue())
        self._contribution(
            kind="template.define",
            target={"type": "template", "name": template},
            value={"value": value},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    @staticmethod
    def _metadata_target(ctx: Any) -> dict[str, Any]:
        if ctx.NET() is not None:
            return {"type": "document"}
        if ctx.place() is not None:
            return {
                "type": "place",
                "name": _decode_name(ctx.place().name().getText()),
            }
        if ctx.transition() is not None:
            return {
                "type": "transition",
                "name": _decode_name(ctx.transition().name().getText()),
            }
        return {"type": "arcHandle", "name": _decode_name(ctx.name().getText())}

    def visitNamedMarking(self, ctx: Any) -> None:
        count, token = self._lower_marking_value(ctx.markingValue())
        self._contribution(
            kind="metadata.named-marking",
            target={"type": "document"},
            value={
                "name": _decode_name(ctx.name().getText()),
                "entries": [
                    {
                        "place": {
                            "type": "place",
                            "name": _decode_name(ctx.place().name().getText()),
                        },
                        "count": count,
                        "token": token,
                    }
                ],
            },
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitMetadataDescription(self, ctx: Any) -> None:
        self._contribution(
            kind="documentation.description",
            target=self._metadata_target(ctx.metadataTarget()),
            value={"text": json.loads(ctx.STRING().getText())},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitMetadataAnnotation(self, ctx: Any) -> None:
        self._contribution(
            kind="documentation.annotation",
            target=self._metadata_target(ctx.metadataTarget()),
            value={
                "key": _decode_name(ctx.name().getText()),
                "value": self._lower_json_value(ctx.jsonValue()),
            },
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitViewPosition(self, ctx: Any) -> None:
        target = ctx.viewTarget()
        subject = (
            {
                "type": "place",
                "name": _decode_name(target.place().name().getText()),
            }
            if target.place() is not None
            else {
                "type": "transition",
                "name": _decode_name(target.transition().name().getText()),
            }
        )
        self._contribution(
            kind="view.position",
            target={"type": "view", "name": _decode_name(ctx.name().getText())},
            value={
                "subject": subject,
                "position": self._lower_json_object(ctx.jsonObject()),
            },
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitViewRoute(self, ctx: Any) -> None:
        names = ctx.name()
        self._contribution(
            kind="view.route",
            target={"type": "arcHandle", "name": _decode_name(names[1].getText())},
            value={
                "view": {"type": "view", "name": _decode_name(names[0].getText())},
                "points": [
                    self._lower_json_value(point)
                    for point in ctx.jsonArray().jsonValue()
                ],
            },
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )

    def visitExtensions(self, ctx: Any) -> None:
        self._contribution(
            kind="document.extensions",
            target={"type": "document"},
            value={"extensions": self._lower_json_object(ctx.jsonObject())},
            start=ctx.start.start,
            end=ctx.stop.stop + 1,
        )


def lower_petrinet_text(text: str, source_name: str = "<memory>") -> dict[str, Any]:
    """Parse one complete Coin Deposit document into portable Contribution IR."""
    source = _SourceMap(source_name, text)
    if (
        text.startswith("\ufeff")
        or "\r" in text.replace("\r\n", "")
        or any(0xD800 <= ord(character) <= 0xDFFF for character in text)
    ):
        raise PetrinetDslError(
            Diagnostic(
                "PN100",
                "source must be strict UTF-8 text without a BOM or bare CR",
                source.span(0, 0),
            )
        )
    lexer = VelocitronPetriNetLexer(InputStream(text))
    listener = _SyntaxListener(source)
    lexer.removeErrorListeners()
    lexer.addErrorListener(listener)
    stream = CommonTokenStream(lexer)
    parser = VelocitronPetriNetParser(stream)
    parser.removeErrorListeners()
    parser.addErrorListener(listener)
    tree = parser.document()
    stream.fill()
    if listener.diagnostics:
        raise PetrinetDslError(listener.diagnostics[0])
    visitor = _LoweringVisitor(source)
    visitor.visit(tree)
    return {
        "format": _FORMAT,
        "version": _VERSION,
        "documentKind": visitor.document_kind or "net",
        "document": {"id": source_name},
        "contributions": visitor.contributions,
    }


def _resolution_error(
    code: str,
    message: str,
    span: Mapping[str, Any] | None = None,
    help: str | None = None,
    related: tuple[tuple[str, Mapping[str, Any]], ...] = (),
) -> PetrinetDslError:
    try:
        if span is not None:
            source = str(span["source"])
            start = span["start"]
            end = span["end"]
            resolved_span = SourceSpan(
                source,
                SourcePosition(start["byteOffset"], start["line"], start["column"]),
                SourcePosition(end["byteOffset"], end["line"], end["column"]),
            )
        else:
            raise KeyError
    except (KeyError, TypeError):
        position = SourcePosition(0, 1, 1)
        resolved_span = SourceSpan("<ir>", position, position)
    related_diagnostics = tuple(
        RelatedDiagnostic(
            related_message,
            SourceSpan(
                str(related_span["source"]),
                SourcePosition(
                    related_span["start"]["byteOffset"],
                    related_span["start"]["line"],
                    related_span["start"]["column"],
                ),
                SourcePosition(
                    related_span["end"]["byteOffset"],
                    related_span["end"]["line"],
                    related_span["end"]["column"],
                ),
            ),
        )
        for related_message, related_span in related
    )
    return PetrinetDslError(
        Diagnostic(code, message, resolved_span, help, related_diagnostics)
    )


def _relative_span(span: Mapping[str, Any], prefix: str, target: str) -> dict[str, Any]:
    start = cast(Mapping[str, Any], span["start"])
    line = cast(int, start["line"])
    column = cast(int, start["column"]) + len(prefix)
    byte_offset = cast(int, start["byteOffset"]) + len(prefix.encode("utf-8"))
    return {
        "source": span["source"],
        "start": {"byteOffset": byte_offset, "line": line, "column": column},
        "end": {
            "byteOffset": byte_offset + len(target.encode("utf-8")),
            "line": line,
            "column": column + len(target),
        },
    }


def _parse_marking_token(
    raw_token: object,
    *,
    message: str,
    span: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw_token, Mapping):
        raise _resolution_error("PN200", message, span)
    token = cast(Mapping[str, Any], raw_token)
    if set(token) == {"template"}:
        raw_template = token["template"]
        if not isinstance(raw_template, Mapping):
            raise _resolution_error("PN200", message, span)
        template = cast(Mapping[str, Any], raw_template)
        name = template.get("name")
        if (
            set(template) != {"type", "name"}
            or template.get("type") != "template"
            or not isinstance(name, str)
            or not name
        ):
            raise _resolution_error("PN200", message, span)
        return {"template": name}
    if set(token) != {"color", "data"}:
        raise _resolution_error("PN200", message, span)
    color = token.get("color")
    raw_data = token.get("data")
    if not isinstance(color, str) or not color or not isinstance(raw_data, Mapping):
        raise _resolution_error("PN200", message, span)
    try:
        data = _untag(cast(Mapping[str, Any], raw_data))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise _resolution_error("PN200", message, span) from error
    if not isinstance(data, dict):
        raise _resolution_error("PN200", message, span)
    return {"color": color, "data": data}


def _materialize_marking_token(
    *,
    place: str,
    token: Mapping[str, Any],
    count: int,
    accepted_colors: Sequence[str],
    templates: Mapping[str, Any],
    span: Mapping[str, Any],
    marking_name: str | None = None,
) -> list[dict[str, Any]]:
    template = token.get("template")
    if isinstance(template, str):
        if template not in templates:
            raise _resolution_error("PN202", f"undefined template ${template}", span)
        if len(accepted_colors) != 1:
            raise _resolution_error(
                "PN202", f"template ${template} color is ambiguous at ({place})", span
            )
        color = accepted_colors[0]
        template_data = templates[template]
        if not isinstance(template_data, dict):
            raise _resolution_error(
                "PN202",
                f"template ${template} data must be a JSON object",
                span,
            )
        data = cast(dict[str, Any], template_data)
    else:
        color = cast(str, token["color"])
        if color not in accepted_colors:
            subject = (
                "marking" if marking_name is None else f"named marking {marking_name!r}"
            )
            raise _resolution_error(
                "PN202",
                f"{subject} token color {color!r} is not accepted by place ({place})",
                span,
            )
        data = cast(dict[str, Any], token["data"])
    return [{"type": color, "data": deepcopy(data)} for _ in range(count)]


def _is_source_span(value: object, expected_source: str) -> bool:
    if not isinstance(value, Mapping):
        return False
    span = cast(dict[str, Any], value)
    if (
        set(span) != {"source", "start", "end"}
        or span["source"] != expected_source
        or _RELATIVE_SOURCE_ID.fullmatch(expected_source) is None
    ):
        return False
    positions: list[tuple[int, int, int]] = []
    for raw_position in (span["start"], span["end"]):
        if not isinstance(raw_position, Mapping):
            return False
        position = cast(dict[str, Any], raw_position)
        if set(position) != {"byteOffset", "line", "column"}:
            return False
        byte_offset = position["byteOffset"]
        line = position["line"]
        column = position["column"]
        if (
            not isinstance(byte_offset, int)
            or isinstance(byte_offset, bool)
            or byte_offset < 0
            or not isinstance(line, int)
            or isinstance(line, bool)
            or line < 1
            or not isinstance(column, int)
            or isinstance(column, bool)
            or column < 1
        ):
            return False
        positions.append((byte_offset, line, column))
    start, end = positions
    return start[0] <= end[0] and start[1:] <= end[1:]


def _source_identity(value: object, source_member: str) -> tuple[str, int, int] | None:
    if not isinstance(value, Mapping):
        return None
    identity = cast(Mapping[str, Any], value)
    if set(identity) != {source_member, "statement", "part"}:
        return None
    source = identity.get(source_member)
    statement = identity.get("statement")
    part = identity.get("part")
    if (
        not isinstance(source, str)
        or _RELATIVE_SOURCE_ID.fullmatch(source) is None
        or not isinstance(statement, int)
        or isinstance(statement, bool)
        or statement < 0
        or not isinstance(part, int)
        or isinstance(part, bool)
        or part < 0
    ):
        return None
    return source, statement, part


def _contribution_identity(value: object) -> tuple[str, int, int] | None:
    return _source_identity(value, "source")


def _arc_identity(value: object) -> tuple[str, int, int] | None:
    return _source_identity(value, "document")


def _resolve_composition_contributions(
    contribution_items: list[Any],
    *,
    aggregate_sources: bool = False,
    net_loader: Callable[[str], Mapping[str, Any] | Path | str] | None = None,
    origin: Path | None = None,
) -> dict[str, Any]:
    header: tuple[str, Mapping[str, Any]] | None = None
    uses: dict[str, tuple[str, Mapping[str, Any]]] = {}
    ordered_uses: list[dict[str, str]] = []
    wires: list[dict[str, dict[str, str]]] = []
    wire_spans: dict[tuple[tuple[str, str], tuple[str, str]], Mapping[str, Any]] = {}
    wire_endpoint_spans: dict[
        tuple[tuple[str, str], tuple[str, str]],
        tuple[Mapping[str, Any], Mapping[str, Any]],
    ] = {}

    for raw_contribution in contribution_items:
        contribution = cast(dict[str, Any], raw_contribution)
        kind = contribution["kind"]
        target = contribution["target"]
        value = contribution["value"]
        span = contribution["span"]
        identity = _contribution_identity(contribution["id"])
        if (
            not isinstance(target, Mapping)
            or not isinstance(value, Mapping)
            or not isinstance(span, Mapping)
            or identity is None
            or identity[2] != 0
        ):
            raise _resolution_error(
                "PN200",
                "invalid composition contribution",
                cast(Mapping[str, Any], span) if isinstance(span, Mapping) else None,
            )
        target = cast(dict[str, Any], target)
        value = cast(dict[str, Any], value)
        span = cast(dict[str, Any], span)
        if not aggregate_sources and identity[1] != contribution["ordinal"]:
            raise _resolution_error(
                "PN200", "invalid composition statement order", span
            )
        if header is None and kind != "document.composition-header":
            raise _resolution_error("PN200", "composition header must be first", span)
        if target != {"type": "document"}:
            raise _resolution_error("PN200", "invalid composition target", span)

        if kind == "document.composition-header":
            namespace = value.get("namespace")
            if (
                (
                    header is not None
                    and (not aggregate_sources or namespace != header[0])
                )
                or set(value) != {"namespace"}
                or not isinstance(namespace, str)
                or not namespace
                or _contains_surrogate(namespace)
            ):
                raise _resolution_error(
                    "PN200", "invalid composition header contribution", span
                )
            if header is None:
                header = (namespace, span)
        elif kind == "composition.use":
            ref, alias = value.get("ref"), value.get("alias")
            if (
                set(value) != {"ref", "alias"}
                or not isinstance(ref, str)
                or not ref
                or not isinstance(alias, str)
                or not alias
                or _TIMER_BIND_IDENTIFIER.fullmatch(alias) is None
                or _contains_surrogate(ref)
            ):
                raise _resolution_error("PN200", "invalid composition use", span)
            existing = uses.get(alias)
            if existing is not None:
                first_ref, first_span = existing
                if first_ref != ref:
                    raise _resolution_error(
                        "PN202",
                        f"conflicting use facts for alias {alias}",
                        span,
                        "use each alias for exactly one referenced net",
                        (
                            (
                                f"first use of alias {alias} was declared here",
                                first_span,
                            ),
                        ),
                    )
                continue
            uses[alias] = (ref, span)
            ordered_uses.append({"ref": ref, "alias": alias})
        elif kind == "composition.wire":
            raw_from, raw_to = value.get("from"), value.get("to")
            if (
                set(value) != {"from", "to"}
                or not isinstance(raw_from, Mapping)
                or not isinstance(raw_to, Mapping)
            ):
                raise _resolution_error("PN200", "invalid composition wire", span)
            typed_from = cast(Mapping[str, Any], raw_from)
            typed_to = cast(Mapping[str, Any], raw_to)
            endpoint_values: list[tuple[str, str]] = []
            endpoint_spans: list[Mapping[str, Any]] = []
            for endpoint in (typed_from, typed_to):
                alias, place, endpoint_span = (
                    endpoint.get("alias"),
                    endpoint.get("place"),
                    endpoint.get("span"),
                )
                if (
                    set(endpoint) != {"alias", "place", "span"}
                    or not isinstance(alias, str)
                    or not alias
                    or not isinstance(place, str)
                    or not place
                    or not _is_source_span(endpoint_span, identity[0])
                ):
                    raise _resolution_error("PN200", "invalid composition wire", span)
                typed_endpoint_span = cast(Mapping[str, Any], endpoint_span)
                if alias not in uses:
                    raise _resolution_error(
                        "PN202",
                        f"wire references unknown alias {alias}",
                        typed_endpoint_span,
                        "declare the alias with use before wiring it",
                    )
                endpoint_values.append((alias, place))
                endpoint_spans.append(typed_endpoint_span)
            source, destination = endpoint_values
            key = (source, destination)
            if key in wire_spans:
                continue
            reverse = (destination, source)
            if reverse in wire_spans:
                raise _resolution_error(
                    "PN202",
                    "conflicting wire facts reverse the same endpoints",
                    span,
                    "remove the reversed wire; wires run output to input",
                    (
                        (
                            "first wire between these endpoints was declared here",
                            wire_spans[reverse],
                        ),
                    ),
                )
            wire_spans[key] = span
            wire_endpoint_spans[key] = (endpoint_spans[0], endpoint_spans[1])
            wires.append(
                {
                    "from": {"net": source[0], "port": source[1]},
                    "to": {"net": destination[0], "port": destination[1]},
                }
            )
        else:
            raise _resolution_error(
                "PN200", f"unsupported contribution kind {kind!r}", span
            )

    if header is None:
        raise _resolution_error("PN200", "missing composition header")
    if not ordered_uses:
        raise _resolution_error(
            "PN202",
            "composition requires at least one use fact",
            header[1],
            'add `use "path" as alias` after the composition header',
        )
    result = {"nets": ordered_uses, "wires": wires}
    if net_loader is None and origin is None:
        return result
    try:
        parse_composition(result, net_loader=net_loader, origin=origin)
    except NetValidationError as error:
        message = str(error)
        default_span = header[1]
        missing_port = re.fullmatch(
            r"net '([^']+)' has no port named '([^']+)'", message
        )
        if missing_port is not None:
            alias, port = missing_port.groups()
            wire_span = next(
                (
                    endpoint_spans[index]
                    for endpoints, endpoint_spans in wire_endpoint_spans.items()
                    for index in range(2)
                    if endpoints[index] == (alias, port)
                ),
                default_span,
            )
            raise _resolution_error(
                "PN202",
                f"wire references unknown port {alias}.({port})",
                wire_span,
                "wire only declared port places",
            ) from None
        bad_direction = re.fullmatch(
            r"wire (source|target) port '([^']+)'\.'([^']+)' "
            r"is not an (output|input) port",
            message,
        )
        if bad_direction is not None:
            role, alias, port, direction = bad_direction.groups()
            endpoint_index = 0 if role == "source" else 1
            wire_span = next(
                (
                    endpoint_spans[endpoint_index]
                    for endpoints, endpoint_spans in wire_endpoint_spans.items()
                    if endpoints[endpoint_index] == (alias, port)
                ),
                default_span,
            )
            help_text = (
                "reverse the wire so it runs from output to input"
                if role == "source"
                else "wire into an input port"
            )
            message = (
                f"wire source endpoint {alias}.({port}) is an input port; "
                "a wire must run from an output port to an input port"
                if role == "source"
                else f"wire {role} {alias}.({port}) must be an {direction} port"
            )
            raise _resolution_error(
                "PN202",
                message,
                wire_span,
                help_text,
            ) from None
        type_mismatch = re.fullmatch(
            r"wire type mismatch: '([^']+)'\.'([^']+)' type '([^']+)' "
            r"!= '([^']+)'\.'([^']+)' type '([^']+)'",
            message,
        )
        if type_mismatch is not None:
            from_alias, from_port, from_type, to_alias, to_port, to_type = (
                type_mismatch.groups()
            )
            wire_span = wire_endpoint_spans.get(
                ((from_alias, from_port), (to_alias, to_port)),
                (default_span, default_span),
            )[1]
            raise _resolution_error(
                "PN202",
                f"wire port colors differ: {from_alias}.({from_port}) carries "
                f"{from_type!r} but {to_alias}.({to_port}) carries {to_type!r}",
                wire_span,
                "connect ports that declare the same color",
            ) from None
        raise _resolution_error(
            "PN500", f"composition reference loading failed: {message}", header[1]
        ) from None
    return result


def resolve_contribution_ir(
    ir: Any,
    *,
    source_ids: Sequence[str] | None = None,
    net_loader: Callable[[str], Mapping[str, Any] | Path | str] | None = None,
    origin: Path | None = None,
) -> dict[str, Any]:
    """Strictly decode v1 Contributions into canonical core JSON."""
    if not isinstance(ir, Mapping):
        raise _resolution_error("PN200", "invalid Contribution IR document shape")
    document_ir = cast(Mapping[str, Any], ir)
    if set(document_ir) != {
        "format",
        "version",
        "documentKind",
        "document",
        "contributions",
    }:
        raise _resolution_error("PN200", "invalid Contribution IR document shape")
    if document_ir["format"] != _FORMAT or document_ir["version"] != _VERSION:
        raise _resolution_error(
            "PN200", "unsupported Contribution IR format or version"
        )
    raw_document = document_ir["document"]
    if not isinstance(raw_document, Mapping):
        raise _resolution_error("PN200", "invalid Contribution IR document")
    document = cast(Mapping[str, Any], raw_document)
    document_id = document.get("id")
    document_kind = document_ir["documentKind"]
    if (
        document_kind not in {"net", "composition"}
        or set(document) != {"id"}
        or not isinstance(document_id, str)
        or _RELATIVE_SOURCE_ID.fullmatch(document_id) is None
    ):
        raise _resolution_error("PN200", "invalid Contribution IR document")
    if source_ids is None:
        allowed_source_ids = frozenset((document_id,))
    elif (
        not source_ids
        or source_ids[0] != document_id
        or len(set(source_ids)) != len(source_ids)
        or any(
            _RELATIVE_SOURCE_ID.fullmatch(source_id) is None for source_id in source_ids
        )
    ):
        raise _resolution_error("PN200", "invalid aggregate source identities")
    else:
        allowed_source_ids = frozenset(source_ids)
    contributions = document_ir["contributions"]
    if not isinstance(contributions, list):
        raise _resolution_error("PN200", "contributions must be an array")
    contribution_items = cast(list[Any], contributions)
    ids: set[tuple[str, int, int]] = set()
    for ordinal, raw_contribution in enumerate(contribution_items):
        if not isinstance(raw_contribution, Mapping):
            raise _resolution_error(
                "PN200", "invalid Contribution IR contribution shape"
            )
        contribution = cast(dict[str, Any], raw_contribution)
        if set(contribution) != {
            "id",
            "kind",
            "ordinal",
            "span",
            "target",
            "value",
        }:
            raise _resolution_error(
                "PN200", "invalid Contribution IR contribution shape"
            )
        ordinal_value = contribution["ordinal"]
        if (
            not isinstance(ordinal_value, int)
            or isinstance(ordinal_value, bool)
            or ordinal_value != ordinal
        ):
            raise _resolution_error(
                "PN200", "contribution ordinals must be contiguous zero-based integers"
            )
        unique_id = _contribution_identity(contribution["id"])
        if unique_id is None:
            raise _resolution_error("PN200", "invalid contribution identity")
        if unique_id[0] not in allowed_source_ids:
            message = (
                "contribution identity source must match document id"
                if source_ids is None
                else "contribution identity source is not in document sources"
            )
            raise _resolution_error("PN200", message)
        if not _is_source_span(contribution["span"], unique_id[0]):
            raise _resolution_error("PN200", "invalid contribution span")
        if unique_id in ids:
            raise _resolution_error("PN200", "duplicate contribution identity")
        ids.add(unique_id)
    if document_kind == "composition":
        return _resolve_composition_contributions(
            contribution_items,
            aggregate_sources=source_ids is not None,
            net_loader=net_loader,
            origin=origin,
        )

    header: Mapping[str, Any] | None = None
    places: list[str] = []
    accepts: dict[str, list[str]] = {}
    declared_accepts: dict[str, tuple[list[str], Mapping[str, Any]]] = {}
    place_appearances: list[str] = []
    declared_places: set[str] = set()
    transitions: list[str] = []
    ports: dict[str, tuple[dict[str, str], Mapping[str, Any]]] = {}
    handlers: dict[str, tuple[str, Mapping[str, Any]]] = {}
    guards: dict[str, tuple[str, Mapping[str, Any]]] = {}
    transition_spans: dict[str, Mapping[str, Any]] = {}
    arcs: list[dict[str, Any]] = []
    arc_declarations: list[dict[str, Any]] = []
    arc_indexes: dict[tuple[str, int, int], int] = {}
    arc_objects: dict[tuple[str, int, int], dict[str, Any]] = {}
    arc_handles: dict[str, list[tuple[str, int, int]]] = {}
    arc_handle_spans: dict[str, Mapping[str, Any]] = {}
    arc_handle_ids: dict[str, tuple[str, int, int]] = {}
    claimed_handle_arc_ids: set[tuple[str, int, int]] = set()
    arc_data: dict[str, tuple[Any, Mapping[str, Any]]] = {}
    arc_cels: dict[str, tuple[str, Mapping[str, Any]]] = {}
    arc_predicates: dict[str, tuple[dict[str, str], Mapping[str, Any]]] = {}
    arc_correlates: dict[str, tuple[str, Mapping[str, Any]]] = {}
    capacities: dict[str, tuple[dict[str, Any], Mapping[str, Any]]] = {}
    timers: dict[str, tuple[str, str, Mapping[str, Any]]] = {}
    timer_binds: dict[str, dict[str, tuple[str, Mapping[str, Any]]]] = {}
    timer_maturities: dict[str, tuple[str, Mapping[str, Any]]] = {}
    priorities: dict[str, tuple[int, Mapping[str, Any]]] = {}
    arc_weights: dict[str, tuple[int, Mapping[str, Any]]] = {}
    transition_orders: dict[str, int] = {}
    transition_order_ranks: dict[int, tuple[str, Mapping[str, Any]]] = {}
    handle_orders: dict[str, int] = {}
    templates: dict[str, Any] = {}
    template_spans: dict[str, Mapping[str, Any]] = {}
    markings: list[tuple[str, dict[str, Any], int, Mapping[str, Any]]] = []
    descriptions: dict[tuple[str, str], tuple[str, Mapping[str, Any]]] = {}
    annotations: dict[tuple[str, str], dict[str, tuple[Any, Mapping[str, Any]]]] = {}
    named_markings: dict[
        str, list[tuple[str, dict[str, Any], int, Mapping[str, Any]]]
    ] = {}
    positions: dict[
        tuple[str, str], dict[str, tuple[dict[str, Any], Mapping[str, Any]]]
    ] = {}
    routes: dict[tuple[str, str], tuple[list[dict[str, Any]], Mapping[str, Any]]] = {}
    extensions: tuple[dict[str, Any], Mapping[str, Any]] | None = None

    for raw_contribution in contribution_items:
        contribution = cast(dict[str, Any], raw_contribution)
        kind = contribution["kind"]
        target = contribution["target"]
        value = contribution["value"]
        span = contribution["span"]
        if (
            not isinstance(target, Mapping)
            or not isinstance(value, Mapping)
            or not isinstance(span, Mapping)
        ):
            raise _resolution_error("PN200", "invalid Contribution IR member")
        target = cast(dict[str, Any], target)
        value = cast(dict[str, Any], value)
        span = cast(dict[str, Any], span)
        if kind == "document.net-header":
            if (
                (
                    header is not None
                    and (source_ids is None or value.get("name") != header.get("name"))
                )
                or target != {"type": "document"}
                or set(value) - {"name", "description"}
                or not isinstance(value.get("name"), str)
            ):
                raise _resolution_error(
                    "PN200", "invalid net header contribution", span
                )
            if header is None:
                header = value
        elif kind == "place.declare":
            name = target.get("name")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "place"
                or not isinstance(name, str)
                or not name
                or value
            ):
                raise _resolution_error(
                    "PN200", "invalid place declaration contribution", span
                )
            declared_places.add(name)
            if name not in place_appearances:
                place_appearances.append(name)
        elif kind == "transition.declare":
            name = target.get("name")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "transition"
                or not isinstance(name, str)
                or not name
                or value
            ):
                raise _resolution_error(
                    "PN200", "invalid transition declaration contribution", span
                )
            if name not in transitions:
                transitions.append(name)
                transition_spans[name] = span
        elif kind == "place.accepts":
            name, raw_colors = target.get("name"), value.get("colors")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "place"
                or not isinstance(name, str)
                or not name
                or set(value) != {"colors"}
                or not isinstance(raw_colors, list)
                or not raw_colors
                or any(
                    not isinstance(color, str) or not color
                    for color in cast(list[object], raw_colors)
                )
                or len(cast(list[object], raw_colors))
                != len(set(cast(list[object], raw_colors)))
            ):
                raise _resolution_error(
                    "PN200", "invalid place accepted-colors contribution", span
                )
            colors = cast(list[str], raw_colors)
            if name not in place_appearances:
                place_appearances.append(name)
            existing = declared_accepts.get(name)
            if existing is not None:
                first_colors, first_span = existing
                if first_colors != colors:
                    raise _resolution_error(
                        "PN202",
                        f"conflicting accepted-color facts for place ({name})",
                        span,
                        "remove one declaration or make both accepted-color lists identical",
                        (("first accepted-color declaration was here", first_span),),
                    )
                continue
            declared_accepts[name] = (colors, span)
        elif kind == "place.port":
            name, direction, color = (
                target.get("name"),
                value.get("direction"),
                value.get("color"),
            )
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "place"
                or not isinstance(name, str)
                or not name
                or set(value) != {"direction", "color"}
                or direction not in {"input", "output"}
                or not isinstance(color, str)
                or not color
            ):
                raise _resolution_error(
                    "PN200", "invalid place port contribution", span
                )
            port = {"direction": cast(str, direction), "type": color}
            existing = ports.get(name)
            if existing is not None:
                first_port, first_span = existing
                if first_port != port:
                    raise _resolution_error(
                        "PN202",
                        f"conflicting port facts for place ({name})",
                        span,
                        "remove one declaration or make both port values identical",
                        (("first port was declared here", first_span),),
                    )
                continue
            ports[name] = (port, span)
        elif kind == "arc.handle":
            name = target.get("name")
            raw_arc_ids = value.get("arcIds")
            contribution_id = _contribution_identity(contribution["id"])
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "arcHandle"
                or not isinstance(name, str)
                or set(value) != {"arcIds"}
                or not isinstance(raw_arc_ids, list)
                or len(cast(list[Any], raw_arc_ids)) < 1
                or contribution_id is None
                or contribution_id[2] != 0
            ):
                raise _resolution_error(
                    "PN200", "invalid arc handle contribution", span
                )
            resolved_ids: list[tuple[str, int, int]] = []
            for raw_arc_id in cast(list[Any], raw_arc_ids):
                arc_id = _arc_identity(raw_arc_id)
                if (
                    arc_id is None
                    or arc_id[0] != contribution_id[0]
                    or arc_id in claimed_handle_arc_ids
                ):
                    raise _resolution_error(
                        "PN200", "invalid arc handle contribution", span
                    )
                resolved_ids.append(arc_id)
            claimed_handle_arc_ids.update(resolved_ids)
            if name in arc_handles:
                if arc_handles[name] != resolved_ids:
                    raise _resolution_error(
                        "PN202",
                        f"conflicting declarations for arc handle @{name}",
                        span,
                        related=(
                            ("first declaration was here", arc_handle_spans[name]),
                        ),
                    )
                continue
            arc_handles[name] = resolved_ids
            arc_handle_ids[name] = contribution_id
            arc_handle_spans[name] = span
        elif kind == "order.transition":
            name, rank = target.get("name"), value.get("rank")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "transition"
                or not isinstance(name, str)
                or not isinstance(rank, int)
                or rank < 1
                or name in transition_orders
            ):
                raise _resolution_error(
                    "PN200", "invalid transition order contribution", span
                )
            if rank in transition_order_ranks:
                first_name, first_span = transition_order_ranks[rank]
                raise _resolution_error(
                    "PN202",
                    f"transition order position {rank} is assigned more than once",
                    span,
                    "assign each explicitly ordered transition a unique positive position",
                    ((f"[{first_name}] first assigned position {rank}", first_span),),
                )
            transition_orders[name] = rank
            transition_order_ranks[rank] = (name, span)
        elif kind == "order.arc-run":
            name, rank = target.get("name"), value.get("rank")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "arcHandle"
                or not isinstance(name, str)
                or not isinstance(rank, int)
                or rank < 1
                or name in handle_orders
            ):
                raise _resolution_error(
                    "PN200", "invalid arc-run order contribution", span
                )
            handle_orders[name] = rank
        elif kind == "place.capacity-per-color-key":
            name = target.get("name")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "place"
                or not isinstance(name, str)
            ):
                raise _resolution_error(
                    "PN200", "invalid place capacity contribution", span
                )
            if set(value) != {"key", "max"}:
                raise _resolution_error(
                    "PN202",
                    f"capacityPerColorKey for ({name}) must contain exactly key and max",
                    span,
                )
            key, maximum = value["key"], value["max"]
            valid_key = _valid_capacity_key(key)
            if not valid_key:
                raise _resolution_error(
                    "PN202",
                    f"capacityPerColorKey key must be a non-empty string or non-empty array of non-empty strings for ({name})",
                    span,
                )
            if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
                raise _resolution_error(
                    "PN202",
                    "capacityPerColorKey max must be an integer greater than or "
                    f"equal to 1; got {maximum!r} for ({name})",
                    span,
                )
            capacity: dict[str, Any] = {"key": key, "max": maximum}
            if name in capacities:
                first, first_span = capacities[name]
                if first != capacity:
                    raise _resolution_error(
                        "PN202",
                        f"conflicting capacityPerColorKey facts for ({name})",
                        span,
                        related=(("first declaration was here", first_span),),
                    )
                continue
            capacities[name] = (capacity, span)
        elif kind == "arc.weight":
            name, weight = target.get("name"), value.get("weight")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "arcHandle"
                or not isinstance(name, str)
                or set(value) != {"weight"}
            ):
                raise _resolution_error(
                    "PN200", "invalid arc weight contribution", span
                )
            if not isinstance(weight, int) or isinstance(weight, bool) or weight < 1:
                raise _resolution_error(
                    "PN202",
                    "arc weight must be an integer greater than or equal to 1; "
                    f"got {weight!r} for @{name}",
                    span,
                )
            if name in arc_weights:
                first, first_span = arc_weights[name]
                if first != weight:
                    raise _resolution_error(
                        "PN202",
                        f"conflicting weight facts for arc @{name}",
                        span,
                        related=(("first declaration was here", first_span),),
                    )
                continue
            arc_weights[name] = (weight, span)
        elif kind == "arc.produce-data":
            name, raw_data = target.get("name"), value.get("data")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "arcHandle"
                or not isinstance(name, str)
                or set(value) != {"data"}
                or not isinstance(raw_data, Mapping)
            ):
                raise _resolution_error(
                    "PN200", "invalid arc produce-data contribution", span
                )
            try:
                data = _untag(cast(Mapping[str, Any], raw_data))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise _resolution_error(
                    "PN200", "invalid tagged JSON arc data value", span
                ) from error
            if name in arc_data:
                first_data, first_span = arc_data[name]
                if not json_values_equal(first_data, data):
                    rendered = json.dumps(
                        first_data,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    raise _resolution_error(
                        "PN202",
                        f"conflicting data facts for arc @{name}",
                        span,
                        related=(
                            (f"first value {rendered} was declared here", first_span),
                        ),
                    )
                continue
            arc_data[name] = (data, span)
        elif kind == "arc.produce-cel":
            name, cel = target.get("name"), value.get("cel")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "arcHandle"
                or not isinstance(name, str)
                or set(value) != {"cel"}
                or not isinstance(cel, str)
                or not cel
            ):
                raise _resolution_error(
                    "PN200", "invalid arc produce-cel contribution", span
                )
            if name in arc_cels:
                first_cel, first_span = arc_cels[name]
                if first_cel != cel:
                    raise _resolution_error(
                        "PN202",
                        f"conflicting data cel facts for arc @{name}",
                        span,
                        related=(("first declaration was here", first_span),),
                    )
                continue
            arc_cels[name] = (cel, span)
        elif kind == "arc.predicate":
            name = target.get("name")
            predicate_kind = value.get("kind")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "arcHandle"
                or not isinstance(name, str)
                or not isinstance(predicate_kind, str)
                or predicate_kind not in {"cel", "handler"}
                or set(value) != {"kind", predicate_kind}
                or not isinstance(value.get(predicate_kind), str)
                or not value[predicate_kind]
            ):
                raise _resolution_error(
                    "PN200", "invalid arc predicate contribution", span
                )
            predicate = {predicate_kind: cast(str, value[predicate_kind])}
            if name in arc_predicates:
                first_predicate, first_span = arc_predicates[name]
                if first_predicate != predicate:
                    raise _resolution_error(
                        "PN202",
                        f"conflicting predicate facts for arc @{name}",
                        span,
                        "remove one declaration or make both predicate values identical",
                        (("first predicate was declared here", first_span),),
                    )
                continue
            arc_predicates[name] = (predicate, span)
        elif kind == "arc.correlate":
            name, cel = target.get("name"), value.get("cel")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "arcHandle"
                or not isinstance(name, str)
                or set(value) != {"cel"}
                or not isinstance(cel, str)
                or not cel
            ):
                raise _resolution_error(
                    "PN200", "invalid arc correlate contribution", span
                )
            if name in arc_correlates:
                first_cel, first_span = arc_correlates[name]
                if first_cel != cel:
                    raise _resolution_error(
                        "PN202",
                        f"conflicting correlate facts for arc @{name}",
                        span,
                        "remove one declaration or make both CEL expressions identical",
                        (("first correlate was declared here", first_span),),
                    )
                continue
            arc_correlates[name] = (cel, span)
        elif kind == "arc.declare":
            raw_color = value.get("color")
            raw_source = value.get("from")
            raw_destination = value.get("to")
            mode = value.get("mode")
            arc_id = _arc_identity(target.get("id"))
            contribution_id = _contribution_identity(contribution["id"])
            if (
                set(target) != {"type", "id"}
                or target.get("type") != "arc"
                or arc_id is None
                or contribution_id is None
                or arc_id != contribution_id
                or not isinstance(raw_color, Mapping)
                or not isinstance(raw_source, Mapping)
                or not isinstance(raw_destination, Mapping)
                or mode not in {"consume", "read", "inhibit", "produce"}
            ):
                raise _resolution_error("PN200", "invalid arc declaration", span)
            color = cast(dict[str, Any], raw_color)
            source = cast(dict[str, Any], raw_source)
            destination = cast(dict[str, Any], raw_destination)
            if not (
                set(color) == {"kind", "value"}
                and color.get("kind") == "explicit"
                and isinstance(color.get("value"), str)
                and color.get("value")
            ):
                raise _resolution_error("PN200", "invalid arc declaration", span)
            is_input: bool = (
                source.get("type") == "place"
                and destination.get("type") == "transition"
                and mode in {"consume", "read", "inhibit"}
            )
            if is_input:
                expected_keys = {"from", "to", "color", "mode"}
                if mode == "consume":
                    expected_keys.add("transitionNameSpan")
                if set(value) != expected_keys:
                    raise _resolution_error(
                        "PN200", "invalid consumed arc declaration", span
                    )
                place, transition = source.get("name"), destination.get("name")
                transition_name_span = value.get("transitionNameSpan", span)
                if mode == "consume" and not _is_source_span(
                    transition_name_span, contribution_id[0]
                ):
                    raise _resolution_error(
                        "PN200", "consumed arc has invalid transitionNameSpan", span
                    )
            elif (
                source.get("type") == "transition"
                and destination.get("type") == "place"
                and mode == "produce"
            ):
                if set(value) != {"from", "to", "color", "mode"}:
                    raise _resolution_error(
                        "PN200", "invalid produced arc declaration", span
                    )
                place, transition = destination.get("name"), source.get("name")
                transition_name_span = span
            else:
                raise _resolution_error(
                    "PN200", "invalid Coin Deposit arc direction", span
                )
            if not isinstance(place, str) or not isinstance(transition, str):
                raise _resolution_error(
                    "PN200", "arc endpoint names must be strings", span
                )
            if place not in place_appearances:
                place_appearances.append(place)
            if transition not in transitions:
                transitions.append(transition)
                transition_spans[transition] = transition_name_span
            arc_declarations.append(
                {
                    "id": arc_id,
                    "place": place,
                    "transition": transition,
                    "isInput": is_input,
                    "mode": mode,
                    "color": color,
                    "span": span,
                    "transitionNameSpan": transition_name_span,
                }
            )
        elif kind == "transition.timer":
            name = target.get("name")
            raw_clock = value.get("clock")
            if not isinstance(raw_clock, Mapping):
                raise _resolution_error(
                    "PN200", "invalid transition timer contribution", span
                )
            clock = cast(Mapping[str, Any], raw_clock)
            cel = value.get("cel")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "transition"
                or not isinstance(name, str)
                or not name
                or set(value) != {"clock", "cel"}
                or set(clock) != {"type", "name"}
                or clock.get("type") != "place"
                or not isinstance(clock.get("name"), str)
                or not clock.get("name")
                or not isinstance(cel, str)
                or not cel
            ):
                raise _resolution_error(
                    "PN200", "invalid transition timer contribution", span
                )
            timer_fact = (cast(str, clock["name"]), cel)
            if name in timers:
                first_clock, first_cel, first_span = timers[name]
                if timer_fact[0] != first_clock:
                    raise _resolution_error(
                        "PN202",
                        f"conflicting timer clock facts for transition [{name}]",
                        span,
                        "remove one declaration or make both timer clock values identical",
                        (("first timer clock was declared here", first_span),),
                    )
                if timer_fact[1] != first_cel:
                    raise _resolution_error(
                        "PN202",
                        f"conflicting timer CEL facts for transition [{name}]",
                        span,
                        "remove one declaration or make both timer CEL values identical",
                        (("first timer CEL was declared here", first_span),),
                    )
                continue
            timers[name] = (*timer_fact, span)
        elif kind == "transition.timer-maturity":
            name = target.get("name")
            maturity = value.get("maturity")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "transition"
                or not isinstance(name, str)
                or not name
                or set(value) != {"maturity"}
                or not isinstance(maturity, str)
                or not maturity
            ):
                raise _resolution_error(
                    "PN200", "invalid transition timer maturity contribution", span
                )
            existing = timer_maturities.get(name)
            if existing is not None and maturity != existing[0]:
                raise _resolution_error(
                    "PN202",
                    f"conflicting timer maturity CEL facts for transition [{name}]",
                    span,
                    "remove one declaration or make both timer maturity CEL values identical",
                    (("first timer maturity CEL was declared here", existing[1]),),
                )
            timer_maturities.setdefault(name, (maturity, span))
        elif kind == "transition.timer-bind":
            name = target.get("name")
            bind_name = value.get("name")
            raw_place = value.get("place")
            if not isinstance(raw_place, Mapping):
                raise _resolution_error(
                    "PN200", "invalid transition timer bind contribution", span
                )
            place = cast(Mapping[str, Any], raw_place)
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "transition"
                or not isinstance(name, str)
                or not name
                or set(value) != {"name", "place"}
                or not isinstance(bind_name, str)
                or _TIMER_BIND_IDENTIFIER.fullmatch(bind_name) is None
                or set(place) != {"type", "name"}
                or place.get("type") != "place"
                or not isinstance(place.get("name"), str)
                or not place.get("name")
            ):
                raise _resolution_error(
                    "PN200", "invalid transition timer bind contribution", span
                )
            binds = timer_binds.setdefault(name, {})
            place_name = cast(str, place["name"])
            if bind_name in binds:
                first_place, first_span = binds[bind_name]
                if place_name != first_place:
                    raise _resolution_error(
                        "PN202",
                        f"conflicting timer bind facts for {bind_name!r} "
                        f"on transition [{name}]",
                        span,
                        "remove one declaration or make both timer bind values identical",
                        (("first timer bind was declared here", first_span),),
                    )
                continue
            binds[bind_name] = (place_name, span)
        elif kind == "transition.priority":
            name = target.get("name")
            priority = value.get("priority")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "transition"
                or not isinstance(name, str)
                or not name
                or set(value) != {"priority"}
                or not isinstance(priority, int)
                or isinstance(priority, bool)
                or priority < 0
            ):
                raise _resolution_error(
                    "PN200", "invalid transition priority contribution", span
                )
            if name in priorities:
                first_priority, first_span = priorities[name]
                if priority != first_priority:
                    raise _resolution_error(
                        "PN202",
                        f"conflicting priority facts for transition [{name}]",
                        span,
                        "remove one declaration or make both priority values identical",
                        (("first priority was declared here", first_span),),
                    )
                continue
            priorities[name] = (priority, span)
        elif kind == "transition.handler":
            name, handler = target.get("name"), value.get("handler")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "transition"
                or not isinstance(name, str)
                or not name
                or set(value) != {"handler"}
                or not isinstance(handler, str)
            ):
                raise _resolution_error(
                    "PN200", "invalid transition handler contribution", span
                )
            existing = handlers.get(name)
            if existing is not None:
                if existing[0] != handler:
                    raise _resolution_error(
                        "PN202",
                        f"conflicting handler facts for transition [{name}]",
                        span,
                        "remove one declaration or make both handler values identical",
                        (("first handler was declared here", existing[1]),),
                    )
                continue
            handlers[name] = (handler, span)
        elif kind == "transition.guard":
            name, guard = target.get("name"), value.get("guard")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "transition"
                or not isinstance(name, str)
                or not name
                or set(value) != {"guard"}
                or not isinstance(guard, str)
                or not guard
            ):
                raise _resolution_error(
                    "PN200", "invalid transition guard contribution", span
                )
            if name in guards:
                first_guard, first_span = guards[name]
                if first_guard != guard:
                    raise _resolution_error(
                        "PN204",
                        f"conflicting guard facts for transition [{name}]",
                        span,
                        "remove one declaration or make both guard values identical",
                        (("first guard was declared here", first_span),),
                    )
                continue
            guards[name] = (guard, span)
        elif kind == "marking.append":
            place = target.get("name")
            count = value.get("count")
            message = "invalid initial marking contribution"
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "place"
                or not isinstance(place, str)
                or not place
                or set(value) != {"count", "token"}
                or not isinstance(count, int)
                or isinstance(count, bool)
                or count < 1
            ):
                raise _resolution_error("PN200", message, span)
            token = _parse_marking_token(value.get("token"), message=message, span=span)
            markings.append((place, token, count, span))
        elif kind == "template.define":
            name, raw_tagged_value = target.get("name"), value.get("value")
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "template"
                or not isinstance(name, str)
                or not isinstance(raw_tagged_value, Mapping)
            ):
                raise _resolution_error(
                    "PN200", "invalid template definition contribution", span
                )
            tagged_value = cast(dict[str, Any], raw_tagged_value)
            try:
                resolved_template = _untag(tagged_value)
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise _resolution_error(
                    "PN200", "invalid tagged JSON template value", span
                ) from error
            if name in templates:
                if not json_values_equal(templates[name], resolved_template):
                    raise _resolution_error(
                        "PN202",
                        f"conflicting definitions for template ${name}",
                        span,
                        related=(("first definition was here", template_spans[name]),),
                    )
                continue
            templates[name] = resolved_template
            template_spans[name] = span
        elif kind == "documentation.description":
            target_type, target_name, text = (
                target.get("type"),
                target.get("name", ""),
                value.get("text"),
            )
            if (
                set(target) not in ({"type"}, {"type", "name"})
                or target_type not in {"document", "place", "transition", "arcHandle"}
                or target_type == "document"
                and set(target) != {"type"}
                or target_type != "document"
                and (
                    set(target) != {"type", "name"} or not isinstance(target_name, str)
                )
                or set(value) != {"text"}
                or not isinstance(text, str)
            ):
                raise _resolution_error(
                    "PN200", "invalid documentation description contribution", span
                )
            key = (cast(str, target_type), cast(str, target_name))
            existing = descriptions.get(key)
            if existing is not None and existing[0] != text:
                raise _resolution_error(
                    "PN202",
                    f"conflicting description facts for {target_type} "
                    + (
                        f"({target_name})"
                        if target_type == "place"
                        else f"[{target_name}]"
                        if target_type == "transition"
                        else f"@{target_name}"
                        if target_type == "arcHandle"
                        else "net"
                    ),
                    span,
                    related=(("first description was declared here", existing[1]),),
                )
            descriptions.setdefault(key, (text, span))
        elif kind == "documentation.annotation":
            target_type, target_name, annotation_key = (
                target.get("type"),
                target.get("name", ""),
                value.get("key"),
            )
            raw_annotation = value.get("value")
            if (
                set(target) not in ({"type"}, {"type", "name"})
                or target_type not in {"document", "place", "transition", "arcHandle"}
                or target_type == "document"
                and set(target) != {"type"}
                or target_type != "document"
                and (
                    set(target) != {"type", "name"} or not isinstance(target_name, str)
                )
                or set(value) != {"key", "value"}
                or not isinstance(annotation_key, str)
                or not annotation_key
                or not isinstance(raw_annotation, Mapping)
            ):
                raise _resolution_error(
                    "PN200", "invalid documentation annotation contribution", span
                )
            if annotation_key == "petrinet.dsl/v1":
                raise _resolution_error(
                    "PN202",
                    "annotation key 'petrinet.dsl/v1' is reserved for compiler-owned metadata",
                    span,
                    "use extensions for opaque full-document data",
                )
            try:
                annotation_value = _untag(cast(Mapping[str, Any], raw_annotation))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise _resolution_error(
                    "PN200", "invalid documentation annotation contribution", span
                ) from error
            identity = (cast(str, target_type), cast(str, target_name))
            facts = annotations.setdefault(identity, {})
            existing = facts.get(annotation_key)
            if existing is not None and not json_values_equal(
                existing[0], annotation_value
            ):
                rendered_target = (
                    f"place ({target_name})"
                    if target_type == "place"
                    else f"transition [{target_name}]"
                    if target_type == "transition"
                    else f"arc @{target_name}"
                    if target_type == "arcHandle"
                    else "net"
                )
                raise _resolution_error(
                    "PN202",
                    f"conflicting annotation {annotation_key} facts for {rendered_target}",
                    span,
                    "remove one declaration or make both annotation values identical",
                    (("first annotation value was declared here", existing[1]),),
                )
            facts.setdefault(annotation_key, (annotation_value, span))
        elif kind == "metadata.named-marking":
            name = value.get("name")
            raw_entries = value.get("entries")
            if (
                target != {"type": "document"}
                or set(value) != {"name", "entries"}
                or not isinstance(name, str)
                or not name
                or name == "initial"
                or not isinstance(raw_entries, list)
            ):
                raise _resolution_error(
                    "PN200", "invalid named marking contribution", span
                )
            parsed_entries: list[
                tuple[str, dict[str, Any], int, Mapping[str, Any]]
            ] = []
            for raw_entry in cast(list[Any], raw_entries):
                if not isinstance(raw_entry, Mapping):
                    raise _resolution_error(
                        "PN200", "invalid named marking contribution", span
                    )
                entry = cast(Mapping[str, Any], raw_entry)
                raw_place, count = entry.get("place"), entry.get("count")
                if not isinstance(raw_place, Mapping):
                    raise _resolution_error(
                        "PN200", "invalid named marking contribution", span
                    )
                place_value = cast(Mapping[str, Any], raw_place)
                place = place_value.get("name")
                if (
                    set(entry) != {"place", "count", "token"}
                    or place_value.get("type") != "place"
                    or set(place_value) != {"type", "name"}
                    or not isinstance(place, str)
                    or not place
                    or not isinstance(count, int)
                    or isinstance(count, bool)
                    or count < 1
                ):
                    raise _resolution_error(
                        "PN200", "invalid named marking contribution", span
                    )
                token = _parse_marking_token(
                    entry.get("token"),
                    message="invalid named marking contribution",
                    span=span,
                )
                parsed_entries.append((place, token, count, span))
            named_markings.setdefault(name, []).extend(parsed_entries)
        elif kind == "view.position":
            view_name, raw_subject, raw_position = (
                target.get("name"),
                value.get("subject"),
                value.get("position"),
            )
            subject = (
                cast(Mapping[str, Any], raw_subject)
                if isinstance(raw_subject, Mapping)
                else None
            )
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "view"
                or not isinstance(view_name, str)
                or not view_name
                or set(value) != {"subject", "position"}
                or subject is None
                or subject.get("type") not in {"place", "transition"}
                or set(subject) != {"type", "name"}
                or not isinstance(subject.get("name"), str)
                or not isinstance(raw_position, Mapping)
            ):
                raise _resolution_error(
                    "PN200", "invalid view position contribution", span
                )
            try:
                decoded_position = _untag(cast(Mapping[str, Any], raw_position))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise _resolution_error(
                    "PN200", "invalid view position contribution", span
                ) from error
            position = (
                cast(dict[str, Any], decoded_position)
                if isinstance(decoded_position, dict)
                else None
            )
            if (
                position is None
                or set(position) != {"x", "y"}
                or any(
                    not isinstance(position[axis], (int, float))
                    or isinstance(position[axis], bool)
                    for axis in ("x", "y")
                )
            ):
                raise _resolution_error(
                    "PN200", "invalid view position contribution", span
                )
            subject_key = f"{subject['type']}:{subject['name']}"
            view_positions = positions.setdefault(
                (view_name, cast(str, subject["type"])), {}
            )
            existing = view_positions.get(subject_key)
            if existing is not None and not json_values_equal(existing[0], position):
                raise _resolution_error(
                    "PN202",
                    f"conflicting position facts for {subject['type']} "
                    + (
                        f"({subject['name']})"
                        if subject["type"] == "place"
                        else f"[{subject['name']}]"
                    )
                    + f" in view {view_name!r}",
                    span,
                    "remove one declaration or make both positions identical",
                    (("first position was declared here", existing[1]),),
                )
            view_positions.setdefault(subject_key, (position, span))
        elif kind == "view.route":
            handle, raw_view, raw_points = (
                target.get("name"),
                value.get("view"),
                value.get("points"),
            )
            view_value = (
                cast(Mapping[str, Any], raw_view)
                if isinstance(raw_view, Mapping)
                else None
            )
            if (
                set(target) != {"type", "name"}
                or target.get("type") != "arcHandle"
                or not isinstance(handle, str)
                or set(value) != {"view", "points"}
                or view_value is None
                or view_value.get("type") != "view"
                or set(view_value) != {"type", "name"}
                or not isinstance(view_value.get("name"), str)
                or not isinstance(raw_points, list)
                or not raw_points
            ):
                raise _resolution_error(
                    "PN200", "invalid view route contribution", span
                )
            points: list[dict[str, Any]] = []
            try:
                for raw_point in cast(list[Any], raw_points):
                    if not isinstance(raw_point, Mapping):
                        raise ValueError
                    decoded_point = _untag(cast(Mapping[str, Any], raw_point))
                    point = (
                        cast(dict[str, Any], decoded_point)
                        if isinstance(decoded_point, dict)
                        else None
                    )
                    if (
                        point is None
                        or set(point) != {"x", "y"}
                        or any(
                            not isinstance(point[axis], (int, float))
                            or isinstance(point[axis], bool)
                            for axis in ("x", "y")
                        )
                    ):
                        raise ValueError
                    points.append(point)
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise _resolution_error(
                    "PN200", "invalid view route contribution", span
                ) from error
            route_key = (cast(str, view_value["name"]), handle)
            existing = routes.get(route_key)
            if existing is not None and not json_values_equal(existing[0], points):
                raise _resolution_error(
                    "PN202",
                    f"conflicting route facts for arc @{handle} in view {view_value['name']!r}",
                    span,
                    related=(("first route was declared here", existing[1]),),
                )
            routes.setdefault(route_key, (points, span))
        elif kind == "document.extensions":
            raw_extensions = value.get("extensions")
            if (
                target != {"type": "document"}
                or set(value) != {"extensions"}
                or not isinstance(raw_extensions, Mapping)
            ):
                raise _resolution_error(
                    "PN200", "invalid document extensions contribution", span
                )
            try:
                decoded_extensions = _untag(cast(Mapping[str, Any], raw_extensions))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise _resolution_error(
                    "PN200", "invalid document extensions contribution", span
                ) from error
            if not isinstance(decoded_extensions, dict):
                raise _resolution_error(
                    "PN200", "invalid document extensions contribution", span
                )
            extension_value = cast(dict[str, Any], decoded_extensions)
            if extensions is not None and not json_values_equal(
                extensions[0], extension_value
            ):
                raise _resolution_error(
                    "PN202",
                    "conflicting document extensions facts",
                    span,
                    related=(("first extensions were declared here", extensions[1]),),
                )
            if extensions is None:
                extensions = (extension_value, span)
        else:
            raise _resolution_error(
                "PN200", f"unsupported contribution kind {kind!r}", span
            )

    places.extend(place_appearances)
    accepts.update(
        (place, list(declared_accepts[place][0]))
        for place in place_appearances
        if place in declared_accepts
    )

    for declaration in arc_declarations:
        arc_id = declaration["id"]
        place = declaration["place"]
        transition = declaration["transition"]
        is_input = declaration["isInput"]
        mode = declaration["mode"]
        span = declaration["span"]
        color = declaration["color"]
        resolved_color = color["value"]
        if place not in accepts:
            accepts[place] = []
        declared = declared_accepts.get(place)
        if declared is not None and resolved_color not in declared[0]:
            raise _resolution_error(
                "PN202",
                f"arc color {resolved_color!r} conflicts with accepted colors declared for ({place})",
                span,
                "add the arc color to the place declaration or change the arc color",
                (("accepted colors were declared here", declared[1]),),
            )
        if resolved_color not in accepts[place]:
            accepts[place].append(resolved_color)
        arc: dict[str, Any] = {
            "from": {"place": place} if is_input else {"transition": transition},
            "to": {"transition": transition} if is_input else {"place": place},
        }
        if is_input:
            consume: dict[str, Any] = {"type": resolved_color}
            if mode != "consume":
                consume["mode"] = mode
            arc["consume"] = consume
        else:
            arc["produce"] = {"destination": place, "type": resolved_color}
        if arc_id in arc_indexes:
            raise _resolution_error("PN200", "duplicate arc identity", span)
        arc_indexes[arc_id] = len(arcs)
        arcs.append(arc)
        arc_objects[arc_id] = arc

    for place in declared_places:
        if place not in accepts:
            accepts[place] = ["token"]

    for handle, resolved_ids in arc_handles.items():
        handle_id = arc_handle_ids[handle]
        declaration_count = sum(arc_id[:2] == handle_id[:2] for arc_id in arc_indexes)
        expected_ids = [
            (handle_id[0], handle_id[1], part)
            for part in range(1, declaration_count + 1)
        ]
        if resolved_ids != expected_ids:
            raise _resolution_error(
                "PN200",
                "invalid arc handle contribution",
                arc_handle_spans[handle],
            )
        if any(arc_id not in arc_indexes for arc_id in resolved_ids):
            raise _resolution_error(
                "PN202",
                f"handle @{handle} refers to an unknown arc",
                arc_handle_spans[handle],
            )

    for place, (_, span) in capacities.items():
        if place not in accepts:
            raise _resolution_error(
                "PN202", f"capacity refers to unknown place ({place})", span
            )

    for place, (port, span) in ports.items():
        if place not in accepts:
            raise _resolution_error(
                "PN202", f"port refers to unknown place ({place})", span
            )
        if port["type"] not in accepts[place]:
            raise _resolution_error(
                "PN202",
                f"port color {port['type']!r} is not accepted by place ({place})",
                span,
                "declare a port color already accepted by the place",
            )

    for handle, (weight, span) in arc_weights.items():
        if handle not in arc_handles:
            raise _resolution_error(
                "PN202", f"weight refers to unknown arc handle @{handle}", span
            )
        input_indexes = [
            arc_indexes[arc_id]
            for arc_id in arc_handles[handle]
            if "consume" in arcs[arc_indexes[arc_id]]
        ]
        if not input_indexes:
            raise _resolution_error(
                "PN202", f"arc weight is not allowed on produce arc @{handle}", span
            )
        if len(input_indexes) != 1:
            raise _resolution_error(
                "PN202",
                f"arc handle @{handle} must identify exactly one input arc for weight",
                span,
            )
        consume = arcs[input_indexes[0]]["consume"]
        if consume.get("mode") == "inhibit" and weight > 1:
            raise _resolution_error(
                "PN202",
                f"arc weight greater than 1 is not allowed on inhibit arc @{handle}",
                span,
            )
        if weight > 1:
            consume["weight"] = weight
    for handle, (data, span) in arc_data.items():
        if handle not in arc_handles:
            raise _resolution_error(
                "PN202", f"data refers to unknown arc handle @{handle}", span
            )
        produce_indexes = [
            arc_indexes[arc_id]
            for arc_id in arc_handles[handle]
            if "produce" in arcs[arc_indexes[arc_id]]
        ]
        if len(produce_indexes) != 1:
            raise _resolution_error(
                "PN202",
                f"arc handle @{handle} must identify exactly one produce arc",
                span,
            )
        arcs[produce_indexes[0]]["produce"]["data"] = data
    for handle, (cel, span) in arc_cels.items():
        if handle in arc_data:
            raise _resolution_error(
                "PN202",
                f"arc @{handle} declares both data and data cel; "
                "they are mutually exclusive",
                span,
                related=(("literal data was declared here", arc_data[handle][1]),),
            )
        if handle not in arc_handles:
            raise _resolution_error(
                "PN202", f"data cel refers to unknown arc handle @{handle}", span
            )
        produce_indexes = [
            arc_indexes[arc_id]
            for arc_id in arc_handles[handle]
            if "produce" in arcs[arc_indexes[arc_id]]
        ]
        if len(produce_indexes) != 1:
            raise _resolution_error(
                "PN202",
                f"arc handle @{handle} must identify exactly one produce arc",
                span,
            )
        try:
            get_default_adapter().compile(cel)
        except Exception:  # noqa: BLE001 - backend-specific errors
            raise _resolution_error(
                "PN203",
                f"invalid CEL data expression for arc @{handle}",
                span,
                "fix the CEL expression syntax",
            ) from None
        arcs[produce_indexes[0]]["produce"]["cel"] = cel
    for handle, (predicate, span) in arc_predicates.items():
        if handle not in arc_handles:
            raise _resolution_error(
                "PN202", f"predicate refers to unknown arc handle @{handle}", span
            )
        consume_indexes = [
            arc_indexes[arc_id]
            for arc_id in arc_handles[handle]
            if "consume" in arcs[arc_indexes[arc_id]]
        ]
        if len(consume_indexes) != 1:
            raise _resolution_error(
                "PN202",
                f"arc handle @{handle} must identify exactly one consume arc",
                span,
            )
        cel = predicate.get("cel")
        if cel is not None:
            try:
                get_default_adapter().compile(cel)
            except Exception:  # noqa: BLE001 - backend-specific errors
                raise _resolution_error(
                    "PN203",
                    f"invalid CEL predicate for arc @{handle}",
                    span,
                    "fix the CEL expression syntax",
                ) from None
        arcs[consume_indexes[0]]["consume"]["predicate"] = predicate
    for handle, (cel, span) in arc_correlates.items():
        if handle not in arc_handles:
            raise _resolution_error(
                "PN202", f"correlate refers to unknown arc handle @{handle}", span
            )
        inhibit_indexes = [
            arc_indexes[arc_id]
            for arc_id in arc_handles[handle]
            if arcs[arc_indexes[arc_id]].get("consume", {}).get("mode") == "inhibit"
        ]
        if len(inhibit_indexes) != 1 or len(arc_handles[handle]) != 1:
            modes = [
                arcs[arc_indexes[arc_id]]["consume"].get("mode", "consume")
                if "consume" in arcs[arc_indexes[arc_id]]
                else "produce"
                for arc_id in arc_handles[handle]
            ]
            mode = modes[0] if len(modes) == 1 else "multiple arcs"
            operator = {
                "consume": "-> (consume)",
                "read": "->? (read)",
                "produce": "-> (produce)",
            }.get(mode, mode)
            raise _resolution_error(
                "PN202",
                "correlate is only allowed on ->0 inhibit arcs; "
                f"@{handle} uses {operator}",
                span,
                "move correlate to a named ->0 inhibit arc",
                ((f"arc @{handle} was declared here", arc_handle_spans[handle]),),
            )
        try:
            get_default_adapter().compile(cel)
        except Exception:  # noqa: BLE001 - backend-specific errors
            raise _resolution_error(
                "PN203",
                f"invalid CEL correlate for arc @{handle}",
                span,
                "fix the CEL expression syntax",
            ) from None
        arcs[inhibit_indexes[0]]["consume"]["correlate"] = {"cel": cel}
    for transition, (_, span) in guards.items():
        if transition not in transitions:
            raise _resolution_error(
                "PN202",
                f"guard refers to unknown transition [{transition}]",
                span,
            )
    for transition, (_, _, span) in timers.items():
        if transition not in transitions:
            raise _resolution_error(
                "PN202",
                f"timer refers to unknown transition [{transition}]",
                span,
            )
    for transition, (_, span) in timer_maturities.items():
        if transition not in transitions:
            raise _resolution_error(
                "PN202",
                f"timer maturity refers to unknown transition [{transition}]",
                span,
            )
        if transition not in timers:
            raise _resolution_error(
                "PN201",
                f"transition [{transition}] has timer maturity but no timer fact",
                span,
                f'add `[{transition}] timer clock (...) cel "..."`',
            )
    for transition, binds in timer_binds.items():
        first_bind_span = next(iter(binds.values()))[1]
        if transition not in transitions:
            raise _resolution_error(
                "PN202",
                f"timer bind refers to unknown transition [{transition}]",
                first_bind_span,
            )
        if transition not in timers:
            raise _resolution_error(
                "PN201",
                f"transition [{transition}] has timer binds but no timer fact",
                first_bind_span,
                f'add `[{transition}] timer clock (...) cel "..."`',
            )
    for transition, (_, span) in handlers.items():
        if transition not in transitions:
            raise _resolution_error(
                "PN202",
                f"handler refers to unknown transition [{transition}]",
                span,
            )
    for transition, (_, span) in priorities.items():
        if transition not in transitions:
            raise _resolution_error(
                "PN202",
                f"priority refers to unknown transition [{transition}]",
                span,
            )
    for (target_type, target_name), (_, fact_span) in descriptions.items():
        if target_type == "place" and target_name not in accepts:
            raise _resolution_error(
                "PN202",
                f"description references unknown place {target_name!r}; metadata facts cannot declare semantic objects",
                fact_span,
            )
        if target_type == "transition" and target_name not in transitions:
            raise _resolution_error(
                "PN202",
                f"description references unknown transition {target_name!r}; metadata facts cannot declare semantic objects",
                fact_span,
            )
        if target_type == "arcHandle" and target_name not in arc_handles:
            raise _resolution_error(
                "PN202",
                f"description references unknown arc handle '@{target_name}'",
                fact_span,
            )
    for (target_type, target_name), facts in annotations.items():
        fact_span = next(iter(facts.values()))[1]
        if target_type == "place" and target_name not in accepts:
            raise _resolution_error(
                "PN202",
                f"annotation references unknown place {target_name!r}; metadata facts cannot declare semantic objects",
                fact_span,
            )
        if target_type == "transition" and target_name not in transitions:
            raise _resolution_error(
                "PN202",
                f"annotation references unknown transition {target_name!r}; metadata facts cannot declare semantic objects",
                fact_span,
            )
        if target_type == "arcHandle" and target_name not in arc_handles:
            raise _resolution_error(
                "PN202",
                f"annotation references unknown arc handle '@{target_name}'",
                fact_span,
            )
    for (view_name, subject_type), view_positions in positions.items():
        known = (
            accepts if subject_type == "place" else {name: None for name in transitions}
        )
        for subject_key, (_, fact_span) in view_positions.items():
            subject_name = subject_key.split(":", 1)[1]
            if subject_name not in known:
                rendered_view_name = (
                    view_name
                    if _TIMER_BIND_IDENTIFIER.fullmatch(view_name)
                    else json.dumps(view_name, ensure_ascii=False)
                )
                rendered_subject_name = (
                    subject_name
                    if _TIMER_BIND_IDENTIFIER.fullmatch(subject_name)
                    else json.dumps(subject_name, ensure_ascii=False)
                )
                target_span = _relative_span(
                    fact_span,
                    f"view {rendered_view_name} position ",
                    f"({rendered_subject_name})"
                    if subject_type == "place"
                    else f"[{rendered_subject_name}]",
                )
                raise _resolution_error(
                    "PN202",
                    f"view {view_name!r} references unknown {subject_type} {subject_name!r}; presentation facts cannot declare semantic objects",
                    target_span,
                    f"declare the {subject_type} in topology before positioning it",
                )
    for (view_name, handle), (_, fact_span) in routes.items():
        if handle not in arc_handles:
            raise _resolution_error(
                "PN202",
                f"view {view_name!r} route references unknown arc handle @{handle}",
                fact_span,
                "declare and name the routed arc before routing it",
            )
    if header is None:
        header = {"name": "unnamed"}
    if transition_orders:
        if set(transition_orders) != set(transitions) or set(
            transition_orders.values()
        ) != set(range(1, len(transitions) + 1)):
            raise _resolution_error(
                "PN202", "transition order must rank every transition exactly once"
            )
        transitions.sort(key=transition_orders.__getitem__)
    if handle_orders:
        if set(handle_orders) != set(arc_handles) or set(handle_orders.values()) != set(
            range(1, len(arc_handles) + 1)
        ):
            raise _resolution_error(
                "PN202", "arc-run order must rank every handle exactly once"
            )
        covered = {arc_id for ids in arc_handles.values() for arc_id in ids}
        if set(arc_indexes) != covered:
            raise _resolution_error(
                "PN202",
                "arc-run order cannot omit arcs from an unhandled chain",
            )
        ordered_arcs: list[dict[str, Any]] = []
        for handle in sorted(handle_orders, key=handle_orders.__getitem__):
            for arc_id in arc_handles[handle]:
                if arc_id not in arc_indexes:
                    raise _resolution_error(
                        "PN202", f"handle @{handle} refers to an unknown arc"
                    )
                ordered_arcs.append(arcs[arc_indexes[arc_id]])
        arcs = ordered_arcs
    initial_marking: dict[str, list[dict[str, Any]]] = {}
    for place, token, count, span in markings:
        if place not in accepts:
            raise _resolution_error(
                "PN202", f"marking refers to unknown place ({place})", span
            )
        initial_marking.setdefault(place, []).extend(
            _materialize_marking_token(
                place=place,
                token=token,
                count=count,
                accepted_colors=accepts[place],
                templates=templates,
                span=span,
            )
        )
    resolved_named_markings: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for marking_name, entries in named_markings.items():
        resolved_marking: dict[str, list[dict[str, Any]]] = {}
        for place, token, count, fact_span in entries:
            if place not in accepts:
                raise _resolution_error(
                    "PN202",
                    f"named marking {marking_name!r} references unknown place {place!r}; marking facts cannot declare semantic objects",
                    fact_span,
                    "declare the place in topology before marking it",
                )
            resolved_marking.setdefault(place, []).extend(
                _materialize_marking_token(
                    place=place,
                    token=token,
                    count=count,
                    accepted_colors=accepts[place],
                    templates=templates,
                    span=fact_span,
                    marking_name=marking_name,
                )
            )
        resolved_named_markings[marking_name] = resolved_marking
    resolved_places: list[dict[str, Any]] = []
    for place in places:
        resolved_place: dict[str, Any] = {"name": place, "accepts": accepts[place]}
        if place in capacities:
            resolved_place["capacityPerColorKey"] = capacities[place][0]
        if place in ports:
            resolved_place["port"] = ports[place][0]
        description_fact = descriptions.get(("place", place))
        if description_fact is not None:
            resolved_place["description"] = description_fact[0]
        annotation_facts = annotations.get(("place", place))
        if annotation_facts:
            resolved_place["annotations"] = {
                key: annotation_facts[key][0] for key in annotation_facts
            }
        resolved_places.append(resolved_place)
    resolved_transitions: list[dict[str, Any]] = []
    for transition in transitions:
        resolved_transition: dict[str, Any] = {"name": transition}
        handler = handlers.get(transition)
        if handler is not None:
            resolved_transition["handler"] = handler[0]
        if transition in guards:
            resolved_transition["guard"] = guards[transition][0]
        if transition in timers:
            clock, cel, _ = timers[transition]
            timer: dict[str, Any] = {"clock": clock, "cel": cel}
            binds = timer_binds.get(transition)
            if binds:
                timer["bind"] = {name: binds[name][0] for name in sorted(binds)}
            resolved_transition["timer"] = timer
            maturity = timer_maturities.get(transition)
            if maturity is not None:
                timer["maturity"] = maturity[0]
        priority = priorities.get(transition)
        if priority is not None and priority[0] != 0:
            resolved_transition["priority"] = priority[0]
        description_fact = descriptions.get(("transition", transition))
        if description_fact is not None:
            resolved_transition["description"] = description_fact[0]
        annotation_facts = annotations.get(("transition", transition))
        if annotation_facts:
            resolved_transition["annotations"] = {
                key: annotation_facts[key][0] for key in annotation_facts
            }
        resolved_transitions.append(resolved_transition)
    current_arc_indexes = {id(arc): index for index, arc in enumerate(arcs)}
    for handle, resolved_ids in arc_handles.items():
        description_fact = descriptions.get(("arcHandle", handle))
        annotation_facts = annotations.get(("arcHandle", handle))
        if description_fact is None and not annotation_facts:
            continue
        for arc_id in resolved_ids:
            arc_position = current_arc_indexes[id(arc_objects[arc_id])]
            if description_fact is not None:
                arcs[arc_position]["description"] = description_fact[0]
            if annotation_facts:
                arcs[arc_position]["annotations"] = {
                    key: annotation_facts[key][0] for key in annotation_facts
                }

    result: dict[str, Any] = {
        "name": header["name"],
        "places": resolved_places,
        "transitions": resolved_transitions,
        "arcs": arcs,
    }
    if markings or not (
        named_markings or positions or routes or extensions is not None
    ):
        result["initialMarking"] = initial_marking
    header_description = header.get("description")
    metadata_description = descriptions.get(("document", ""))
    if metadata_description is not None:
        if (
            header_description is not None
            and header_description != metadata_description[0]
        ):
            raise _resolution_error(
                "PN202",
                "conflicting description facts for net",
                metadata_description[1],
            )
        result["description"] = metadata_description[0]
    elif header_description is not None:
        result["description"] = header_description
    document_annotations = annotations.get(("document", ""))
    if document_annotations:
        result["annotations"] = {
            key: document_annotations[key][0] for key in document_annotations
        }
    metadata_handles = {
        target_name
        for target_type, target_name in {*descriptions, *annotations}
        if target_type == "arcHandle"
    } | {handle for _, handle in routes}
    views: dict[str, dict[str, Any]] = {}
    for (view_name, _), view_positions in positions.items():
        view = views.setdefault(view_name, {"positions": {}, "routes": {}})
        view["positions"].update(
            {subject: position[0] for subject, position in view_positions.items()}
        )
    for (view_name, handle), (points, _) in routes.items():
        view = views.setdefault(view_name, {"positions": {}, "routes": {}})
        view["routes"][handle] = {"style": "orthogonal", "points": points}
    if resolved_named_markings or views or extensions is not None or metadata_handles:
        payload_handles: dict[str, Any] = {}
        for handle in sorted(metadata_handles):
            resolved_ids = arc_handles[handle]
            if len(resolved_ids) != 1:
                raise _resolution_error(
                    "PN202",
                    f"metadata arc handle '@{handle}' must identify exactly one arc",
                    arc_handle_spans[handle],
                )
            arc_object = arc_objects[resolved_ids[0]]
            current_index = current_arc_indexes[id(arc_object)]
            resolved_arc = arcs[current_index]
            if "consume" in resolved_arc:
                fingerprint = {
                    "from": deepcopy(resolved_arc["from"]),
                    "to": deepcopy(resolved_arc["to"]),
                    "type": resolved_arc["consume"]["type"],
                    "mode": resolved_arc["consume"].get("mode", "consume"),
                }
            else:
                fingerprint = {
                    "from": deepcopy(resolved_arc["from"]),
                    "to": deepcopy(resolved_arc["to"]),
                    "type": resolved_arc["produce"]["type"],
                    "mode": "produce",
                }
            payload_handles[handle] = {
                "index": current_index,
                "fingerprint": fingerprint,
            }
        payload: dict[str, Any] = {
            "arcHandles": payload_handles,
            "markings": resolved_named_markings,
            "views": views,
            "extensions": extensions[0] if extensions is not None else {},
        }
        result.setdefault("annotations", {})["petrinet.dsl/v1"] = payload

    try:
        parse_net(result)
    except NetValidationError as error:
        parser_message = str(error)
        for transition, (clock, _, timer_span) in timers.items():
            if f"transition {transition!r}" not in parser_message:
                continue
            if "references undeclared" in parser_message:
                raise _resolution_error(
                    "PN201",
                    f"timer clock place ({clock}) for transition "
                    f"[{transition}] is not declared",
                    timer_span,
                    "declare the clock place in topology before using it in a timer",
                ) from None
            for bind_name, (place, bind_span) in timer_binds.get(
                transition, {}
            ).items():
                if f"bind variable {bind_name!r}" not in parser_message:
                    continue
                if bind_name == "clock":
                    raise _resolution_error(
                        "PN202",
                        f"timer bind name 'clock' is reserved on transition "
                        f"[{transition}]",
                        bind_span,
                        "choose a bind name other than 'clock'",
                    ) from None
                raise _resolution_error(
                    "PN202",
                    f"timer bind {bind_name!r} on transition [{transition}] "
                    f"names place ({place}), but that place does not feed "
                    f"[{transition}] through a consume or read arc",
                    bind_span,
                    "bind the variable to a consume or read input place",
                ) from None
            if "invalid CEL expression" in parser_message:
                raise _resolution_error(
                    "PN203",
                    f"invalid CEL timer for transition [{transition}]",
                    timer_span,
                    "fix the CEL expression syntax",
                ) from None
        raise
    return result
