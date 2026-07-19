"""Red acceptance coverage for the first Coin Deposit DSL capability."""

from __future__ import annotations

from antlr4 import CommonTokenStream, InputStream, Token
from antlr4.Recognizer import Recognizer
from antlr4.error.ErrorListener import ErrorListener
from antlr4.error.Errors import RecognitionException

from velocitron.dsl.generated.VelocitronPetriNetLexer import VelocitronPetriNetLexer
from velocitron.dsl.generated.VelocitronPetriNetParser import VelocitronPetriNetParser


class _CollectingErrorListener(ErrorListener):
    def __init__(self) -> None:
        self.diagnostics: list[tuple[int, int, str]] = []

    def syntaxError(
        self,
        recognizer: Recognizer,
        offendingSymbol: Token | None,
        line: int,
        column: int,
        msg: str,
        e: RecognitionException | None,
    ) -> None:
        self.diagnostics.append((line, column, msg))


def test_coin_deposit_document_has_no_syntax_diagnostics() -> None:
    """Red bite: the EOF-only grammar diagnoses ``net`` until Coin Deposit syntax exists."""
    # given: the canonical Coin Deposit source, including its forward template reference
    source = """\
net coin_deposit \"Coin deposit\"

(coin_slot) -coin-> [accept_coin] -coin-> (cash_box)

[accept_coin] handler \"accept_coin\"

marking initial (coin_slot) <- $inserted_coin
$inserted_coin: coin {}
"""
    diagnostics = _CollectingErrorListener()
    lexer = VelocitronPetriNetLexer(InputStream(source))
    lexer.removeErrorListeners()
    lexer.addErrorListener(diagnostics)
    parser = VelocitronPetriNetParser(CommonTokenStream(lexer))
    parser.removeErrorListeners()
    parser.addErrorListener(diagnostics)

    # when: the generated document entry point consumes the complete source
    parser.document()

    # then: Coin Deposit is accepted without lexer or parser diagnostics
    assert diagnostics.diagnostics == []
