import pytest

from pco.assets import MAIA3_5M, AssetError, asset_path, stockfish_binary
from pco.config import load_settings


@pytest.fixture(scope="session")
def installed_settings():
    """Settings for the real installed assets ($PCO_HOME or platform default)."""
    return load_settings()


@pytest.fixture(scope="session")
def maia3_prior(installed_settings):
    path = asset_path(installed_settings, MAIA3_5M)
    if not path.exists():
        pytest.skip("Maia-3 weights not installed (run `pco setup`)")
    from pco.priors.maia3 import Maia3Prior

    return Maia3Prior(path, threads=installed_settings.torch_threads)


@pytest.fixture(scope="session")
def stockfish_path(installed_settings):
    try:
        return stockfish_binary(installed_settings)
    except AssetError:
        pytest.skip("Stockfish not installed (run `pco setup`)")
