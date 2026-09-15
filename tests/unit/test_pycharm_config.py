from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[2]


def load_configuration(filename: str) -> ElementTree.Element:
    root = ElementTree.parse(ROOT / ".run" / filename).getroot()
    configuration = root.find("configuration")
    assert configuration is not None
    return configuration


def option(configuration: ElementTree.Element, name: str) -> str | None:
    element = configuration.find(f"./option[@name='{name}']")
    return None if element is None else element.get("value")


def test_application_run_configuration_uses_project_sdk_and_uvicorn():
    configuration = load_configuration("DeepDoc Agent.run.xml")
    assert configuration.get("type") == "PythonConfigurationType"
    assert option(configuration, "IS_MODULE_SDK") == "true"
    assert option(configuration, "WORKING_DIRECTORY") == "$PROJECT_DIR$"
    assert option(configuration, "MODULE_MODE") == "true"
    assert option(configuration, "MODULE_NAME") == "uvicorn"
    assert option(configuration, "PARAMETERS") == (
        "app.main:app --host 127.0.0.1 --port 8000 --reload"
    )
    envs = {env.get("name"): env.get("value") for env in configuration.findall("./envs/env")}
    assert envs["DEEPDOC_LLM_PROVIDER"] == "extractive"
    assert envs["DEEPDOC_DATABASE_PATH"] == "data/deepdoc.db"


def test_pytest_run_configuration_targets_all_tests():
    configuration = load_configuration("All Tests.run.xml")
    assert configuration.get("factoryName") == "py.test"
    assert option(configuration, "IS_MODULE_SDK") == "true"
    assert option(configuration, "WORKING_DIRECTORY") == "$PROJECT_DIR$"
    assert option(configuration, "_new_target") == '"$PROJECT_DIR$/tests"'
    assert option(configuration, "_new_targetType") == '"PATH"'
