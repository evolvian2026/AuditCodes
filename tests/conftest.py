import pytest

from auditcodes.exec.languages import LANGUAGES
from auditcodes.exec.runner import LocalRunner
from auditcodes.models import Language


@pytest.fixture(scope="session")
def runner() -> LocalRunner:
    return LocalRunner()


def require_language(language: Language) -> None:
    if not LANGUAGES[language].available():
        pytest.skip(f"toolchain for {language.value} not available")
