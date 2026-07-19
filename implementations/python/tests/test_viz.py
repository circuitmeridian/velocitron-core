"""Tests for the graphviz DOT renderer (`velocitron.viz`).

These assert on the semantic markers each net-schema feature contributes to
the DOT output (styles, glyphs, label fragments), not on byte-exact DOT: the
renderer's contract is "every feature is visually distinguishable", and the
markers are the distinguishers.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from velocitron.parser import parse_composition, parse_net
from velocitron.viz import (
    RenderStyle,
    composition_ports_dot,
    composition_to_dot,
    legend_dot,
    main,
    net_to_dot,
)

# A single net exercising every place/transition/arc feature the renderer
# distinguishes: port facets, initial marking, guard + priority, all three
# consume modes, weight, both predicate forms, a literal produce payload, and
# a computed CEL produce fallback (ADR 0023).
FEATURE_NET: dict[str, Any] = {
    "name": "feature-net",
    "places": [
        {
            "name": "in_port",
            "accepts": ["job"],
            "port": {"direction": "input", "type": "job"},
        },
        {
            "name": "out_port",
            "accepts": ["job"],
            "port": {"direction": "output", "type": "job"},
        },
        {
            "name": "buffer",
            "accepts": ["job", "flag"],
            "description": "a buffer of jobs",
        },
    ],
    "transitions": [
        {"name": "work", "handler": "work_handler"},
        {
            "name": "gated",
            "handler": "gated_handler",
            "guard": "gate_guard",
            "priority": 7,
        },
    ],
    "arcs": [
        {
            "from": {"place": "in_port"},
            "to": {"transition": "work"},
            "consume": {
                "type": "job",
                "weight": 2,
                "predicate": {"cel": 'kind == "batch"'},
            },
        },
        {
            "from": {"place": "buffer"},
            "to": {"transition": "work"},
            "consume": {"type": "flag", "mode": "inhibit"},
        },
        {
            "from": {"place": "buffer"},
            "to": {"transition": "gated"},
            "consume": {
                "type": "job",
                "mode": "read",
                "predicate": {"handler": "job_ready"},
            },
        },
        {
            "from": {"transition": "work"},
            "to": {"place": "out_port"},
            "produce": {
                "type": "job",
                "destination": "out_port",
                "data": {"fixed": True},
            },
        },
        {
            "from": {"transition": "gated"},
            "to": {"place": "buffer"},
            "produce": {
                "type": "flag",
                "destination": "buffer",
                "cel": '{"count": binding.buffer[0].count - 1}',
            },
        },
    ],
    "initialMarking": {
        "buffer": [
            {"type": "job", "data": {}},
            {"type": "job", "data": {}},
        ]
    },
}


def _feature_dot() -> str:
    return net_to_dot(parse_net(FEATURE_NET))


class TestNetRendering:
    def test_unnamed_net_omits_graph_title(self):
        # given: the minimal unnamed net produced by headerless DSL source
        net = parse_net(
            {"name": "unnamed", "places": [], "transitions": [], "arcs": []}
        )

        # when: rendered with either label style
        html_dot = net_to_dot(net)
        plain_dot = net_to_dot(net, style=RenderStyle(html_labels=False))

        # then: neither graph carries a top title
        assert "labelloc=t" not in html_dot
        assert "labelloc=t" not in plain_dot

    def test_named_net_keeps_graph_title(self):
        # given: a minimal explicitly named net
        net = parse_net(
            {"name": "release", "places": [], "transitions": [], "arcs": []}
        )

        # when: rendered with either label style
        html_dot = net_to_dot(net)
        plain_dot = net_to_dot(net, style=RenderStyle(html_labels=False))

        # then: each graph carries its named title
        assert "label=<<b>release</b>>; labelloc=t;" in html_dot
        assert 'label="release"; labelloc=t;' in plain_dot

    def test_port_places_render_direction_and_double_ring(self):
        # given: a net with an input port, an output port, and an internal place
        # when: rendered to DOT
        dot = _feature_dot()

        # then: each port names its direction and type and is double-ringed
        assert "port input &#183; job" in dot
        assert "port output &#183; job" in dot
        assert dot.count("peripheries=2") == 2
        # and: the internal place carries its accepted types, no port line
        assert "&#10216;job, flag&#10217;" in dot

    def test_initial_marking_renders_token_count_by_type(self):
        # given: a net whose buffer place starts with two job tokens
        # when: rendered to DOT
        dot = _feature_dot()

        # then: the place carries a marking line with the count and type
        assert "● × 2 job" in dot

    def test_marking_can_be_omitted(self):
        # given: the same net
        # when: rendered with include_marking=False
        dot = net_to_dot(parse_net(FEATURE_NET), include_marking=False)

        # then: no marking line appears
        assert "●" not in dot

    def test_transition_renders_handler_guard_and_priority(self):
        # given: a guarded, prioritized transition and a plain one
        # when: rendered to DOT
        dot = _feature_dot()

        # then: both handler refs appear as call-shaped labels
        assert "work_handler()" in dot
        assert "gated_handler()" in dot
        # and: the guard ref and (activated, non-reserved) priority render on
        # the guarded box
        assert "guard: gate_guard?" in dot
        assert "priority 7" in dot
        # and: the stale "(reserved)" qualifier is gone (ADR 0014 activated the
        # priority field via the built-in "priority" firing policy)
        assert "(reserved)" not in dot

    def test_consume_modes_render_distinct_styles(self):
        # given: consume, inhibit, and read arcs
        # when: rendered to DOT
        dot = _feature_dot()

        # then: the inhibit arc uses the crimson dashed open-dot glyph
        assert "arrowhead=odot" in dot
        assert "no flag" in dot
        # and: the read arc is labeled read and styled blue dashed
        assert "read job" in dot
        assert 'color="#1f6feb", style=dashed' in dot

    def test_weight_and_both_predicate_forms_render(self):
        # given: a weighted CEL-predicated arc and a handler-predicated arc
        # when: rendered to DOT
        dot = _feature_dot()

        # then: the weight prefixes the type and the CEL text appears
        assert "2 &#215; job" in dot
        assert 'kind == "batch"' in dot
        # and: the named predicate renders as a pred: ref
        assert "pred: job_ready" in dot

    def test_produce_literal_data_renders(self):
        # given: a produce template carrying a literal data payload
        # when: rendered to DOT
        dot = _feature_dot()

        # then: the payload is shown alongside the produced type
        assert '+ data {"fixed": true}' in dot

    def test_produce_computed_cel_renders_marked_as_computed(self):
        # given: a produce template carrying a computed CEL fallback (ADR 0023)
        # when: rendered to DOT
        dot = _feature_dot()

        # then: the expression is shown alongside the produced type, marked as
        # computed via the DSL's `data cel` vocabulary rather than a literal
        assert '+ data cel {"count": binding.buffer[0].count - 1}' in dot


TRADITIONAL_NET: dict[str, Any] = {
    "name": "traditional",
    "places": [
        {
            "name": "source",
            "accepts": ["token"],
            "port": {"direction": "input", "type": "token"},
        },
        {"name": "mixed", "accepts": ["token", "job"]},
        {"name": "sink", "accepts": ["token"]},
        {"name": "clock", "accepts": ["token"]},
    ],
    "transitions": [
        {
            "name": "advance",
            "guard": "ready",
            "timer": {"clock": "clock", "cel": "clock.now >= 0"},
            "priority": 4,
        },
        {"name": "inspect", "handler": "inspect_handler"},
        {"name": "blocked", "handler": "blocked_handler"},
    ],
    "arcs": [
        {
            "from": {"place": "source"},
            "to": {"transition": "advance"},
            "consume": {"type": "token"},
        },
        {
            "from": {"transition": "advance"},
            "to": {"place": "sink"},
            "produce": {"type": "token", "destination": "sink"},
        },
        {
            "from": {"place": "source"},
            "to": {"transition": "inspect"},
            "consume": {
                "type": "token",
                "mode": "read",
                "weight": 2,
                "predicate": {"cel": "true"},
            },
            "description": "generic read",
        },
        {
            "from": {"place": "source"},
            "to": {"transition": "blocked"},
            "consume": {
                "type": "token",
                "mode": "inhibit",
                "predicate": {"cel": "false"},
                "correlate": {"cel": "7 == 7"},
            },
            "description": "generic inhibit",
        },
        {
            "from": {"transition": "inspect"},
            "to": {"place": "sink"},
            "produce": {
                "type": "token",
                "destination": "sink",
                "data": {"ready": True},
            },
            "description": "generic output",
        },
        {
            "from": {"transition": "blocked"},
            "to": {"place": "sink"},
            "produce": {
                "type": "token",
                "destination": "sink",
                "cel": '{"flag": 1}',
            },
        },
    ],
    "initialMarking": {
        "source": [
            {"type": "token", "data": {}},
            {"type": "token", "data": {}},
            {"type": "token", "data": {"id": 7}},
        ]
    },
}


class TestTraditionalRendering:
    @pytest.mark.parametrize("html_labels", [True, False])
    def test_generic_token_is_hidden_from_places_ports_and_marking(
        self, html_labels: bool
    ) -> None:
        dot = net_to_dot(
            parse_net(TRADITIONAL_NET),
            style=RenderStyle(html_labels=html_labels),
        )

        assert "token" not in dot
        assert "● × 2" in dot
        data = '{"id":7}' if html_labels else '{\\"id\\":7}'
        assert f"● {data}" in dot
        if html_labels:
            assert "port input</font>" in dot
            assert "&#10216;job&#10217;" in dot
            assert "&#10216;&#10217;" not in dot
        else:
            assert "port input" in dot
            assert "port input ·" not in dot
            assert "⟨job⟩" in dot
            assert "⟨⟩" not in dot

    @pytest.mark.parametrize("html_labels", [True, False])
    def test_handlerless_transition_keeps_its_other_facts(
        self, html_labels: bool
    ) -> None:
        net = parse_net(TRADITIONAL_NET)
        assert net.transitions[0].handler is None
        dot = net_to_dot(net, style=RenderStyle(html_labels=html_labels))

        advance_line = next(line for line in dot.splitlines() if '"advance" [' in line)
        assert "advance" in advance_line
        assert "guard: ready?" in advance_line
        timer = "timer: clock.now &gt;= 0" if html_labels else "timer: clock.now >= 0"
        assert timer in advance_line
        assert "priority 4" in advance_line
        assert "None()" not in advance_line
        assert "<br/><br/>" not in advance_line
        assert "\\n\\n" not in advance_line
        assert "inspect_handler()" in dot

    @pytest.mark.parametrize("html_labels", [True, False])
    def test_generic_arc_details_remain_visible(self, html_labels: bool) -> None:
        dot = net_to_dot(
            parse_net(TRADITIONAL_NET),
            style=RenderStyle(html_labels=html_labels),
        )

        times = "&#215;" if html_labels else "×"
        assert f"read 2 {times}" in dot
        assert "true" in dot
        assert "no" in dot
        assert "false" in dot
        assert "7 == 7" in dot
        assert "arrowhead=odot" in dot
        assert 'color="#1f6feb", style=dashed' in dot
        data = '{"ready": true}' if html_labels else '{\\"ready\\": true}'
        assert f"+ data {data}" in dot
        # and: a computed CEL fallback stays visible on a Generic produce arc
        # even though the redundant generic type label is suppressed
        cel_expr = '{"flag": 1}' if html_labels else '{\\"flag\\": 1}'
        assert f"+ data cel {cel_expr}" in dot
        assert 'tooltip="generic read"' in dot
        assert 'tooltip="generic inhibit"' in dot
        assert 'tooltip="generic output"' in dot

    @pytest.mark.parametrize("html_labels", [True, False])
    def test_unadorned_generic_arcs_are_bare_edges(self, html_labels: bool) -> None:
        dot = net_to_dot(
            parse_net(TRADITIONAL_NET),
            style=RenderStyle(html_labels=html_labels),
        )

        assert '    "source" -> "advance";' in dot
        assert '    "advance" -> "sink";' in dot
        assert '    "source" -> "advance" [];' not in dot
        assert '    "advance" -> "sink" [];' not in dot

    @pytest.mark.parametrize("html_labels", [True, False])
    def test_explicit_color_and_handler_remain_visible(self, html_labels: bool) -> None:
        dot = net_to_dot(
            parse_net(FEATURE_NET),
            style=RenderStyle(html_labels=html_labels),
        )

        angle = "&#10216;job, flag&#10217;" if html_labels else "⟨job, flag⟩"
        times = "&#215;" if html_labels else "×"
        assert angle in dot
        assert "work_handler()" in dot
        assert f"2 {times} job" in dot
        assert "● × 2 job" in dot


# A net with a fusion-annotated place (CONTEXT.md "Fusion place") touched by
# three transitions through produce, consume, and inhibit arcs.
FUSION_NET: dict[str, Any] = {
    "name": "fusion-net",
    "places": [
        {"name": "window", "accepts": ["tok"], "annotations": {"fusion": True}},
        {"name": "src", "accepts": ["job"]},
        {"name": "dst", "accepts": ["job"]},
    ],
    "transitions": [
        {"name": "open", "handler": "open_h"},
        {"name": "close", "handler": "close_h"},
        {"name": "idle", "handler": "idle_h"},
    ],
    "arcs": [
        {
            "from": {"place": "src"},
            "to": {"transition": "open"},
            "consume": {"type": "job"},
        },
        {
            "from": {"transition": "open"},
            "to": {"place": "window"},
            "produce": {"type": "tok", "destination": "window"},
        },
        {
            "from": {"place": "window"},
            "to": {"transition": "close"},
            "consume": {"type": "tok"},
        },
        {
            "from": {"place": "window"},
            "to": {"transition": "idle"},
            "consume": {"type": "tok", "mode": "inhibit"},
        },
        {
            "from": {"transition": "close"},
            "to": {"place": "dst"},
            "produce": {"type": "job", "destination": "dst"},
        },
    ],
    "initialMarking": {"window": [{"type": "tok", "data": {}}]},
}


class TestFusionRendering:
    def test_fusion_place_renders_one_local_instance_per_transition(self):
        # given: a fusion-annotated place touched by three transitions
        # when: rendered to DOT
        dot = net_to_dot(parse_net(FUSION_NET))

        # then: each connected transition gets a local instance, no hub node
        assert '"window@open"' in dot
        assert '"window@close"' in dot
        assert '"window@idle"' in dot
        assert '"window" [' not in dot
        # and: instances carry the fusion styling and the place's own label
        assert dot.count('style=dashed, color="gray40"') == 3
        assert "<b>window</b>" in dot
        # and: arcs route to the local instances, keeping their mode styles
        assert '"open" -> "window@open"' in dot
        assert '"window@close" -> "close"' in dot
        assert '"window@idle" -> "idle" [label=' in dot
        assert "arrowhead=odot" in dot
        # and: the marking renders on each instance (one logical place)
        assert dot.count("● tok") == 3

    def test_transition_with_several_arcs_shares_one_instance(self):
        # given: the fusion place plus a produce arc back from close
        doc: dict[str, Any] = json.loads(json.dumps(FUSION_NET))
        doc["arcs"].append(
            {
                "from": {"transition": "close"},
                "to": {"place": "window"},
                "produce": {"type": "tok", "destination": "window"},
            }
        )

        # when: rendered to DOT
        dot = net_to_dot(parse_net(doc))

        # then: close's consume and produce share the single window@close node
        assert dot.count('\n    "window@close" [') == 1
        assert '"window@close" -> "close"' in dot
        assert '"close" -> "window@close"' in dot

    def test_render_fusion_false_restores_the_single_node(self):
        # given: the same fusion-annotated net
        # when: rendered with render_fusion=False
        dot = net_to_dot(parse_net(FUSION_NET), render_fusion=False)

        # then: the place renders as one ordinary node, no local instances
        assert '"window" [' in dot
        assert "window@" not in dot


class TestDocStyle:
    def test_plain_labels_swap_html_for_quoted_labels(self):
        # given: the feature net
        # when: rendered with plain labels
        dot = net_to_dot(parse_net(FEATURE_NET), style=RenderStyle(html_labels=False))

        # then: no HTML-like labels remain, content survives in plain form
        assert "label=<" not in dot
        assert "⟨job, flag⟩" in dot
        assert "port input · job" in dot
        assert "● × 2 job" in dot
        assert "guard: gate_guard?" in dot
        assert "no flag" in dot
        assert "read job" in dot
        assert "2 × job" in dot
        assert 'kind == \\"batch\\"' in dot
        # and: edge mode styling is unchanged
        assert "arrowhead=odot" in dot
        assert 'color="#1f6feb", style=dashed' in dot

    def test_tooltips_can_be_omitted(self):
        # given: a net with a described place (renders a tooltip by default)
        assert "tooltip=" in net_to_dot(parse_net(FEATURE_NET))

        # when: rendered with tooltips off
        dot = net_to_dot(parse_net(FEATURE_NET), style=RenderStyle(tooltips=False))

        # then: no tooltips remain
        assert "tooltip=" not in dot


# Producer/consumer pair from spec/composition.md's worked example — the
# minimal two-net, one-wire composition.
PRODUCER_NET: dict[str, Any] = {
    "name": "producer",
    "places": [
        {"name": "work", "accepts": ["task"]},
        {
            "name": "out",
            "accepts": ["task"],
            "port": {"direction": "output", "type": "task"},
        },
    ],
    "transitions": [{"name": "produce", "handler": "produce_handler"}],
    "arcs": [
        {
            "from": {"place": "work"},
            "to": {"transition": "produce"},
            "consume": {"type": "task"},
        },
        {
            "from": {"transition": "produce"},
            "to": {"place": "out"},
            "produce": {"type": "task", "destination": "out"},
        },
    ],
}

CONSUMER_NET: dict[str, Any] = {
    "name": "consumer",
    "places": [
        {
            "name": "in",
            "accepts": ["task"],
            "port": {"direction": "input", "type": "task"},
        },
        {"name": "done", "accepts": ["task"]},
    ],
    "transitions": [{"name": "consume", "handler": "consume_handler"}],
    "arcs": [
        {
            "from": {"place": "in"},
            "to": {"transition": "consume"},
            "consume": {"type": "task"},
        },
        {
            "from": {"transition": "consume"},
            "to": {"place": "done"},
            "produce": {"type": "task", "destination": "done"},
        },
    ],
}


def _write_composition(tmp_path: Path) -> Path:
    (tmp_path / "producer.json").write_text(json.dumps(PRODUCER_NET))
    (tmp_path / "consumer.json").write_text(json.dumps(CONSUMER_NET))
    composition = {
        "nets": [
            {"ref": str(tmp_path / "producer.json"), "alias": "prod"},
            {"ref": str(tmp_path / "consumer.json"), "alias": "cons"},
        ],
        "wires": [
            {
                "from": {"net": "prod", "port": "out"},
                "to": {"net": "cons", "port": "in"},
            }
        ],
    }
    path = tmp_path / "composition.json"
    path.write_text(json.dumps(composition))
    return path


class TestCompositionRendering:
    def test_clusters_and_wire(self, tmp_path: Path) -> None:
        # given: the spec's producer/consumer composition
        path = _write_composition(tmp_path)
        composition = parse_composition(json.loads(path.read_text()))

        # when: rendered to DOT
        dot = composition_to_dot(composition)

        # then: each aliased net becomes a cluster labeled by its alias
        assert "subgraph cluster_0" in dot
        assert "<b>prod" in dot
        assert "<b>cons" in dot
        # and: nodes are alias-qualified
        assert '"prod.work"' in dot
        assert '"cons.done"' in dot
        # and: the wire joins the two port places with the wire style
        assert '"prod.out" -> "cons.in" [penwidth=2.2, color="#6a3d9a"];' in dot

    def test_ports_only_collapses_nets_to_boundary_ports(self, tmp_path: Path) -> None:
        # given: the spec's producer/consumer composition
        path = _write_composition(tmp_path)
        composition = parse_composition(json.loads(path.read_text()))

        # when: rendered ports-only
        dot = composition_ports_dot(composition)

        # then: each aliased net is a cluster holding only its port places
        assert "subgraph cluster_0" in dot
        assert '"prod.out"' in dot
        assert '"cons.in"' in dot
        # and: internal places, transitions, and arcs are gone
        assert "prod.work" not in dot
        assert "cons.done" not in dot
        assert "produce_handler" not in dot
        assert "consume_handler" not in dot
        # and: the wire still joins the two ports with the wire style
        assert '"prod.out" -> "cons.in" [penwidth=2.2, color="#6a3d9a"];' in dot


class TestLegend:
    def test_legend_exercises_every_visual_convention(self):
        # when: the legend is rendered
        dot = legend_dot()

        # then: it carries an exemplar of each feature's marker
        for marker in [
            "port input",
            "port output",
            "guard: guard_ref?",
            "priority 5",
            "● × 2 colorA",  # initial marking
            "arrowhead=odot",  # inhibit
            "read colorA",  # read
            "2 &#215; colorA",  # weight
            "pred: predicate_ref",  # named predicate
            'field == "value"',  # inline CEL
            "+ data",  # literal produce payload
            "+ data cel",  # computed CEL produce fallback (ADR 0023)
            'penwidth=2.2, color="#6a3d9a"',  # wire
            'style=dashed, color="gray40"',  # fusion-place local instance
        ]:
            assert marker in dot
        # and: no stale "(reserved)" priority qualifier survives (ADR 0014)
        assert "(reserved)" not in dot


class TestCli:
    def test_net_to_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # given: a net document on disk
        path = tmp_path / "net.json"
        path.write_text(json.dumps(FEATURE_NET))

        # when: the CLI runs with no output flag
        exit_code = main([str(path)])

        # then: the DOT digraph lands on stdout
        assert exit_code == 0
        assert capsys.readouterr().out.startswith("digraph net {")

    def test_direction_override_accepts_lowercase_equals_syntax(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # given: a standalone net document
        path = tmp_path / "net.json"
        path.write_text(json.dumps(FEATURE_NET))

        # when: the CLI requests left-to-right rendering
        exit_code = main([str(path), "--direction=lr"])

        # then: Graphviz receives the uppercase rank direction
        assert exit_code == 0
        assert "rankdir=LR;" in capsys.readouterr().out

    def test_composition_defaults_to_top_to_bottom_direction(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # given: a composition document on disk
        path = _write_composition(tmp_path)

        # when: the CLI renders it with no direction flag
        exit_code = main([str(path)])

        # then: the composition uses the same top-to-bottom default as nets
        assert exit_code == 0
        assert "rankdir=TB;" in capsys.readouterr().out

    def test_composition_to_dot_file_with_relative_refs(self, tmp_path: Path) -> None:
        # given: a composition whose net refs are relative to its own directory
        path = _write_composition(tmp_path)
        doc: dict[str, Any] = json.loads(path.read_text())
        for entry in doc["nets"]:
            entry["ref"] = entry["ref"].rsplit("/", 1)[-1]
        path.write_text(json.dumps(doc))
        out = tmp_path / "composition.dot"

        # when: the CLI renders it to a file
        exit_code = main([str(path), "-o", str(out)])

        # then: the file holds the clustered composition digraph
        assert exit_code == 0
        assert out.read_text().startswith("digraph composition {")

    def test_merged_composition(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # given: the same composition
        path = _write_composition(tmp_path)

        # when: the CLI renders it with --merged
        exit_code = main([str(path), "--merged"])

        # then: the output is a single net digraph with the fused port place
        # rendered as per-transition local instances — merge_nets tags every
        # fused place with annotations.fusion, with no manual tagging in the
        # source documents
        assert exit_code == 0
        dot = capsys.readouterr().out
        assert dot.startswith("digraph net {")
        assert '"prod.out@prod.produce"' in dot
        assert '"prod.out@cons.consume"' in dot
        assert '"prod.out" [' not in dot
        assert "subgraph" not in dot

    def test_ports_only_composition(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # given: the same composition
        path = _write_composition(tmp_path)

        # when: the CLI renders it with --ports-only
        exit_code = main([str(path), "--ports-only"])

        # then: the output is the ports-only wiring digraph
        assert exit_code == 0
        dot = capsys.readouterr().out
        assert dot.startswith("digraph composition_ports {")
        assert '"prod.out" -> "cons.in"' in dot
        assert "prod.work" not in dot

    def test_doc_preset_with_fence(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # given: a net document on disk
        path = tmp_path / "net.json"
        path.write_text(json.dumps(FEATURE_NET))

        # when: the CLI renders it with the markdown-embedding preset + fence
        exit_code = main([str(path), "--doc", "--fence"])

        # then: the output is a ```dot fence around plain-label, tooltip-free DOT
        assert exit_code == 0
        out = capsys.readouterr().out
        assert out.startswith("```dot\ndigraph net {")
        assert out.endswith("}\n```\n")
        assert "label=<" not in out
        assert "tooltip=" not in out

    def test_no_fusion_flag(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # given: a net with a fusion-annotated place
        path = tmp_path / "net.json"
        path.write_text(json.dumps(FUSION_NET))

        # when: the CLI renders it with --no-fusion
        exit_code = main([str(path), "--no-fusion"])

        # then: the place renders as a single hub node
        assert exit_code == 0
        out = capsys.readouterr().out
        assert '"window" [' in out
        assert "window@" not in out

    def test_merged_composition_keeps_fusion_rendering(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # given: a composition whose consumer net carries a fusion place
        consumer: dict[str, Any] = json.loads(json.dumps(CONSUMER_NET))
        consumer["places"].append(
            {"name": "window", "accepts": ["task"], "annotations": {"fusion": True}}
        )
        consumer["arcs"].append(
            {
                "from": {"transition": "consume"},
                "to": {"place": "window"},
                "produce": {"type": "task", "destination": "window"},
            }
        )
        (tmp_path / "producer.json").write_text(json.dumps(PRODUCER_NET))
        (tmp_path / "consumer.json").write_text(json.dumps(consumer))
        path = tmp_path / "composition.json"
        path.write_text(
            json.dumps(
                {
                    "nets": [
                        {"ref": "producer.json", "alias": "prod"},
                        {"ref": "consumer.json", "alias": "cons"},
                    ],
                    "wires": [
                        {
                            "from": {"net": "prod", "port": "out"},
                            "to": {"net": "cons", "port": "in"},
                        }
                    ],
                }
            )
        )

        # when: the CLI renders the merged net
        exit_code = main([str(path), "--merged"])

        # then: the annotation survived the merge and drives local instances
        assert exit_code == 0
        out = capsys.readouterr().out
        assert '"cons.window@cons.consume"' in out
        assert '"cons.window" [' not in out


class TestMarkedStandaloneCli:
    def test_named_marking_replaces_the_authored_initial_marking(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # given: a standalone Net with both an initial and a named marking
        document: dict[str, Any] = json.loads(json.dumps(FEATURE_NET))
        document["annotations"] = {
            "petrinet.dsl/v1": {
                "markings": {
                    "queued": {"buffer": [{"type": "flag", "data": {"id": "queued"}}]}
                }
            }
        }
        path = tmp_path / "net.json"
        path.write_text(json.dumps(document))

        # when: the named marking is selected
        exit_code = main([str(path), "--marking", "queued", "--plain-labels"])

        # then: it completely replaces rather than augments the default marking
        assert exit_code == 0
        output = capsys.readouterr().out
        assert '● flag {\\"id\\":\\"queued\\"}' in output
        assert "● × 2 job" not in output

    def test_named_marking_from_petrinet_source_replaces_initial_marking(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # given: a DSL Net whose named marking is emitted as reserved metadata
        path = tmp_path / "marked.petrinet"
        path.write_text(
            """\
net marked "Marked source"

(buffer) -job-> [consume]
[consume] handler "consume"

marking initial (buffer) <- $initial_token
marking queued (buffer) <- $queued_token
$initial_token: job {"id": "initial"}
$queued_token: job {"id": "queued"}
"""
        )

        # when: the CLI selects the authored DSL named marking
        exit_code = main([str(path), "--marking", "queued", "--plain-labels"])

        # then: the compiled metadata follows the JSON named-marking path
        assert exit_code == 0
        output = capsys.readouterr().out
        assert '● job {\\"id\\":\\"queued\\"}' in output
        assert '● job {\\"id\\":\\"initial\\"}' not in output

    def test_inline_marking_reports_the_lexically_first_invalid_token(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # given: a selected marking with a bad known-place token before an unknown place
        path = tmp_path / "net.json"
        path.write_text(json.dumps(FEATURE_NET))
        inline = (
            '{"z_undefined":[{"type":"job","data":{}}],'
            '"buffer":[{"type":"not-accepted","data":{}}]}'
        )

        # when: the inline marking is validated for rendering
        exit_code = main([str(path), "--marking-json", inline])

        # then: the canonical first violation is the lexical buffer token
        assert exit_code == 1
        assert capsys.readouterr().err == (
            'velocitron-viz: error: invalid marking at place "buffer", token 0: '
            'type "not-accepted" is not accepted (accepted colors: "flag", "job")\n'
        )

    def test_inline_marking_rejects_duplicate_keys_at_nested_depth(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # given: an otherwise valid inline marking whose token data repeats a key
        path = tmp_path / "net.json"
        path.write_text(json.dumps(FEATURE_NET))
        inline = '{"buffer":[{"type":"job","data":{"id":1,"id":2}}]}'

        # when: the strict JSON argument is decoded
        exit_code = main([str(path), "--marking-json", inline])

        # then: duplicate keys are rejected instead of silently last-wins decoding
        assert exit_code == 1
        assert capsys.readouterr().err == (
            "velocitron-viz: error: invalid inline marking JSON: "
            'duplicate object key "id"\n'
        )

    def test_marking_selectors_are_mutually_exclusive_and_standalone_only(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # given: a valid standalone Net and a composition document
        net_path = tmp_path / "net.json"
        net_path.write_text(json.dumps(FEATURE_NET))
        composition_path = _write_composition(tmp_path)

        # when: incompatible selector combinations are parsed
        with pytest.raises(SystemExit) as mutually_exclusive:
            main([str(net_path), "--marking", "queued", "--no-marking"])
        with pytest.raises(SystemExit) as composition_selector:
            main([str(composition_path), "--marking-json", "{}"])

        # then: argparse rejects both invocations before rendering
        assert mutually_exclusive.value.code == 2
        assert composition_selector.value.code == 2
        errors = capsys.readouterr().err
        assert "--no-marking" in errors
        assert "standalone Nets" in errors

    def test_help_documents_marking_selection_for_agents(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # given: the visualization CLI
        # when: its generated help is requested
        with pytest.raises(SystemExit) as exit_info:
            main(["--help"])

        # then: the selector grammar and no-indirection limit are discoverable
        assert exit_info.value.code == 0
        help_text = " ".join(capsys.readouterr().out.split())
        assert "--marking NAME" in help_text
        assert "--direction {tb,lr,bt,rl}" in help_text
        assert "--rankdir {tb,lr,bt,rl}" in help_text
        assert "velocitron-viz design.petrinet --direction=lr" in help_text
        assert "--marking-json JSON" in help_text
        assert (
            "Default: render the authored initial marking. If none is declared, "
            "render an empty marking." in help_text
        )
        assert "no file or stdin indirection" in help_text.lower()
        assert "Mutually exclusive with --marking" in help_text
        assert "velocitron-viz design.petrinet --marking queued" in help_text


class TestMarkedPlaceGrammar:
    def test_marking_groups_full_tokens_in_canonical_order_for_html_and_plain(self):
        # given: one Place with repeated and insertion-order-scrambled token values
        document: dict[str, Any] = {
            "name": "grouped-marking",
            "places": [{"name": "buffer", "accepts": ["job", "notice"]}],
            "transitions": [],
            "arcs": [],
            "initialMarking": {
                "buffer": [
                    {"type": "notice", "data": {}},
                    {"type": "job", "data": {"id": 2}},
                    {"type": "job", "data": {"id": 1}},
                    {"type": "job", "data": {"id": 1}},
                ]
            },
        }

        # when: the same selected marking renders in both label styles
        html_dot = net_to_dot(parse_net(document))
        plain_dot = net_to_dot(
            parse_net(document), style=RenderStyle(html_labels=False)
        )

        # then: full canonical data controls grouping and lexical group order
        assert (
            '<table border="1" cellborder="0" cellspacing="0" cellpadding="4"'
            in html_dot
        )
        assert 'style="rounded" color="#1a7f37"' in html_dot
        assert (
            html_dot.index('● × 2 job {"id":1}')
            < html_dot.index('● job {"id":2}')
            < html_dot.index("● notice")
        )
        # and: plain labels carry the same rows without a synthetic heading
        assert (
            plain_dot.index('● × 2 job {\\"id\\":1}')
            < plain_dot.index('● job {\\"id\\":2}')
            < plain_dot.index("● notice")
        )
        assert "marking:" not in plain_dot

    def test_marking_caps_groups_after_full_value_sorting(
        self,
    ) -> None:
        # given: nine distinct, lexically sortable token groups in one Place
        colors = [f"token-{index}" for index in range(9)]
        document: dict[str, Any] = {
            "name": "capped-marking",
            "places": [{"name": "buffer", "accepts": colors}],
            "transitions": [],
            "arcs": [],
            "initialMarking": {
                "buffer": [{"type": color, "data": {}} for color in reversed(colors)]
            },
        }

        # when: the Place renders with plain labels
        dot = net_to_dot(parse_net(document), style=RenderStyle(html_labels=False))

        # then: the first eight sorted groups render followed by one overflow row
        assert "● token-0" in dot
        assert "● token-7" in dot
        assert "● token-8" not in dot
        assert "… + 1 more token group" in dot

    def test_marking_caps_type_and_data_at_the_contract_boundaries(self) -> None:
        # given: one token whose type and canonical data exceed both display limits
        type_ = "t" * 65
        data_value = "d" * 200
        canonical_data = f'{{"value":"{data_value}"}}'
        document: dict[str, Any] = {
            "name": "truncated-marking",
            "places": [{"name": "buffer", "accepts": [type_]}],
            "transitions": [],
            "arcs": [],
            "initialMarking": {
                "buffer": [{"type": type_, "data": {"value": data_value}}]
            },
        }

        # when: the selected marking renders as a plain DOT label
        dot = net_to_dot(parse_net(document), style=RenderStyle(html_labels=False))

        # then: type keeps 63 scalars and data keeps 159 before the ellipsis
        assert f"● {type_[:63]}…" in dot
        displayed_data = (canonical_data[:159] + "…").replace('"', '\\"')
        assert displayed_data in dot

    def test_marked_place_html_label_wraps_badge_in_single_outer_table(self) -> None:
        """A Graphviz HTML-like label must be EITHER text OR one <table>; a
        marked place mixes both (name text + marking badge). The label must
        therefore nest everything under one borderless outer <table> — no
        top-level text may precede the badge.

        Bite: reverting `_label_attr`'s badge branch to the plain
        ``'<br/>'.join(lines)`` reintroduces the illegal top-level
        ``<br/><table`` sequence (text-then-table), failing this assertion —
        the same syntax error that makes `dot` exit 1.
        """
        # given: one Place carrying a marking (so a badge <table> is emitted)
        document: dict[str, Any] = {
            "name": "marked",
            "places": [{"name": "buffer", "accepts": ["job"]}],
            "transitions": [],
            "arcs": [],
            "initialMarking": {"buffer": [{"type": "job", "data": {"id": 1}}]},
        }

        # when: the marking renders as an HTML-like label
        html_dot = net_to_dot(parse_net(document))

        # then: no top-level text precedes the badge table (the invalid form)
        assert "<br/><table" not in html_dot
        # and: the label nests under one borderless outer table
        assert (
            '<table border="0" cellborder="0" cellspacing="0" cellpadding="0">'
            in html_dot
        )
        # and: the badge table survives, nested inside the wrapper
        assert '<table border="1" cellborder="0" cellspacing="0" cellpadding="4"' in (
            html_dot
        )

    @pytest.mark.skipif(shutil.which("dot") is None, reason="graphviz `dot` absent")
    def test_marked_place_html_dot_renders_with_exit_zero(self) -> None:
        """End-to-end: the emitted DOT for a marked place must parse cleanly
        through Graphviz. `dot` exits 1 on a malformed HTML-like label; strict
        backends (e.g. Kroki) turn that non-zero exit into a hard 400.

        Bite: the pre-fix ``<br/>``-joined label (text-then-<table>) makes
        `dot -Tsvg` exit 1; this construction-bite asserts exit 0, so the
        badge-wrapping branch of `_label_attr` is the sole barrier to the
        non-zero exit.
        """
        # given: a Place with a marking, rendered as an HTML-like-label DOT
        document: dict[str, Any] = {
            "name": "marked",
            "places": [{"name": "buffer", "accepts": ["job"]}],
            "transitions": [],
            "arcs": [],
            "initialMarking": {"buffer": [{"type": "job", "data": {"id": 1}}]},
        }
        html_dot = net_to_dot(parse_net(document))

        # when: Graphviz renders the DOT to SVG
        result = subprocess.run(
            ["dot", "-Tsvg"],
            input=html_dot,
            capture_output=True,
            text=True,
        )

        # then: `dot` accepts the label and exits cleanly
        assert result.returncode == 0, result.stderr
