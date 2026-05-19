import traders


def test_version_exists():
    assert hasattr(traders, "__version__")
    assert isinstance(traders.__version__, str)
    assert traders.__version__ == "0.0.0"
