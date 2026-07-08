"""Card 22: lucy.testing is the one public source of truth for simulators.

The deterministic simulators (ADR 0003) are shipped from lucy.testing. The old
module paths keep working for one release but emit DeprecationWarning so SDK and
plugin authors migrate to the documented location (ADR 0010).
"""

import importlib

import pytest

MOVED = [
    ("lucy.voice", "LocalSttSimulator"),
    ("lucy.voice", "LocalTtsSimulator"),
    ("lucy.mcp", "LocalMcpCommandTransport"),
    ("lucy.rag", "LocalEmbeddingFixture"),
    ("lucy.observe", "InMemoryOtelSpanExporter"),
    ("lucy.metrics", "LocalMetricEventChannel"),
]


def test_all_simulators_importable_from_lucy_testing():
    from lucy.testing import (
        InMemoryOtelSpanExporter,
        LocalEmbeddingFixture,
        LocalMcpCommandTransport,
        LocalMetricEventChannel,
        LocalSttSimulator,
        LocalTtsSimulator,
    )

    classes = [
        LocalSttSimulator,
        LocalTtsSimulator,
        LocalMcpCommandTransport,
        LocalEmbeddingFixture,
        InMemoryOtelSpanExporter,
        LocalMetricEventChannel,
    ]
    assert all(isinstance(cls, type) for cls in classes)


@pytest.mark.parametrize("module_name, attr", MOVED)
def test_old_import_paths_warn_and_resolve_to_testing(module_name, attr):
    module = importlib.import_module(module_name)
    import lucy.testing as testing

    with pytest.warns(DeprecationWarning):
        obj = getattr(module, attr)
    assert obj is getattr(testing, attr)


@pytest.mark.parametrize("module_name", sorted({m for m, _ in MOVED}))
def test_unknown_attribute_still_raises_attribute_error(module_name):
    module = importlib.import_module(module_name)
    with pytest.raises(AttributeError):
        module.DefinitelyNotARealSymbol
