# Velocitron for Python

The Python package is the reference implementation of Velocitron: a toolkit for authoring, validating, visualizing, simulating, and running typed colored Petri nets through the shared JSON format and `.petrinet` language.

## Requirements and installation

Velocitron requires Python 3.12 or newer. Install the published package from
PyPI:

```console
python -m pip install velocitron
```

To install from a source checkout instead:

```console
cd implementations/python
python -m pip install .
```

## Command-line examples

```console
velocitron validate workflow.petrinet
velocitron to-json workflow.petrinet > workflow.json
velocitron explain workflow.petrinet --format text
velocitron-viz workflow.petrinet --output workflow.dot
```

For the cross-language overview and format documentation, see the [repository documentation](https://github.com/circuitmeridian/velocitron-core#readme).

## Alpha status

Version 0.1.0 is alpha software. APIs, schemas, the DSL, diagnostics, and capabilities may change without notice. Velocitron is not production-ready and provides no compatibility, migration, support-response, or maintenance guarantees. Support is best-effort. Feedback and reproducible defect reports are welcome; external code contributions are not currently accepted.

Velocitron has no formal security-support program or currently supported
private vulnerability-reporting channel. Do not use it in production,
security-sensitive, or otherwise high-consequence contexts. The
[repository security policy](https://github.com/circuitmeridian/velocitron-core/blob/main/SECURITY.md)
describes the current boundary.

## Authorship and acknowledgment

Velocitron is authored solely by Matthew R. Scott. Henrique Bastos is acknowledged for collaboration toward an interoperable Petri-net specification; this acknowledgment does not indicate shared authorship.

Licensed under the MIT License.
