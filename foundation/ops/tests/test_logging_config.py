"""The queue memory-governance evidence model (docs/OPERATIONS.md "The
execution queue's memory governance") assumes its `logger.info(...)`
eviction/unload lines reach `docker compose logs worker`. Django ships NO
`LOGGING` setting by default, so its own fallback applies: only WARNING and
above from a non-`django` logger reaches the console (Python's own
last-resort handler on the root logger). Every INFO eviction/unload/
precautionary-call line the worker and the engine adapters emit was
therefore silently dropped on every deployment.

`config/settings.py` now ships an explicit `LOGGING` dict so `models.queue`,
`models.contracts.engines`, and `models.registry` -- the three logger
namespaces the feature actually uses (`models/queue/worker.py`,
`models/queue/backend.py`, `models/queue/claim.py`, `models/contracts/
engines/{ollama,comfyui,whisper}.py`, `models/registry/bindings.py` each
open with `logger = logging.getLogger(__name__)`) -- reach a stderr
console handler at INFO. `FARABUNKER_QUEUE_LOG_LEVEL` overrides that one
level; an unset or invalid value falls back to INFO rather than crashing
boot, since a typo here governs verbosity, not correctness.

`models.registry` was added after the other two shipped: its own
footprint-provenance refusal (`_record_footprint`) logs its mild branch
at INFO and its severe branch at WARNING, and only the WARNING half
reached the console before this namespace was added here -- the INFO
half fell back to the root logger's WARNING floor and was silently
dropped, on a large model a multi-gigabyte refusal nobody saw.

ONE HANDLER IN THE WHOLE TREE, attached only at `root`. The three feature
loggers carry no dedicated handler of their own and are left on
`propagate`'s own default (`True`): a record climbs to `root` and prints
there, exactly once. `config/settings.py`'s own comment above `LOGGING`
records why -- giving each logger its OWN handler plus `propagate: False`
needs a second handler at `root` (for every other namespace's WARNING+
lines) and `propagate: False` to stop them from both firing for the
same record, and that `propagate: False` is exactly what silently breaks
`caplog`-based tests: pytest's own capturing handler is attached to
`root` alone, so ~20 pre-existing tests under `models/queue/tests/`
asserting on `models.queue.worker`'s log lines via `caplog` went dark
under that shape. Routing everything through the single `root` handler
reaches the identical operator-facing outcome without that regression.
"""
from __future__ import annotations

import logging
import logging.config

from django.conf import settings as django_settings

QUEUE_LOG_ENV = "FARABUNKER_QUEUE_LOG_LEVEL"


class TestLoggingSettingShape:
    """Pure dict-shape assertions -- no emission, same spirit as
    `test_compose_env_anchor.py`'s file-text checks: prove the
    configuration itself routes correctly rather than trusting that it
    happens to behave when exercised."""

    def test_version_and_disable_existing_loggers(self):
        assert django_settings.LOGGING["version"] == 1
        # Module-level `logger = logging.getLogger(__name__)` -- the
        # pattern every logger in this codebase uses -- runs at IMPORT
        # time, potentially before Django ever calls dictConfig. Disabling
        # "existing" loggers would silence exactly those.
        assert django_settings.LOGGING["disable_existing_loggers"] is False

    def test_a_console_handler_writes_to_stderr_with_a_plain_single_line_format(self):
        handlers = django_settings.LOGGING["handlers"]
        assert "console" in handlers
        console = handlers["console"]
        assert console["class"] == "logging.StreamHandler"
        assert console.get("stream") == "ext://sys.stderr"
        formatter_name = console["formatter"]
        fmt = django_settings.LOGGING["formatters"][formatter_name]["format"]
        assert fmt == "%(asctime)s %(levelname)s %(name)s: %(message)s"

    def test_root_is_warning_and_uses_the_console_handler(self):
        root = django_settings.LOGGING["root"]
        assert root["level"] == "WARNING"
        assert "console" in root["handlers"]

    def test_models_queue_is_routed_at_info(self):
        logger_cfg = django_settings.LOGGING["loggers"]["models.queue"]
        assert logger_cfg["level"] == "INFO"
        # NO DEDICATED HANDLER, and propagate stays on its own default
        # (True) -- see this module's docstring and `config/settings.py`'s
        # own comment above `LOGGING` for why: it reaches `root`'s single
        # handler by climbing the hierarchy rather than by carrying a
        # second handler of its own, which is what keeps a line from
        # printing twice AND keeps `caplog`-based tests working.
        assert logger_cfg.get("handlers", []) == []
        assert logger_cfg.get("propagate", True) is True

    def test_models_contracts_engines_is_routed_at_info(self):
        logger_cfg = django_settings.LOGGING["loggers"]["models.contracts.engines"]
        assert logger_cfg["level"] == "INFO"
        assert logger_cfg.get("handlers", []) == []
        assert logger_cfg.get("propagate", True) is True

    def test_models_registry_is_routed_at_info(self):
        # The registry's footprint-provenance refusal (`_record_footprint`)
        # is the only `logger.info(...)` call anywhere under
        # `models/registry/` outside its tests -- one rare,
        # operator-actionable line, not a per-request one -- so raising
        # this namespace surfaces exactly that and nothing chattier.
        logger_cfg = django_settings.LOGGING["loggers"]["models.registry"]
        assert logger_cfg["level"] == "INFO"
        assert logger_cfg.get("handlers", []) == []
        assert logger_cfg.get("propagate", True) is True

    def test_django_loggers_are_not_touched(self):
        """Non-negotiable per the brief: Django's own default logging
        behaviour (and DEBUG behaviour) is untouched. This settings module
        adds no `"django"` (or `"django.*"`) entry of its own."""
        loggers = django_settings.LOGGING["loggers"]
        assert "django" not in loggers
        assert not any(name.startswith("django.") for name in loggers)


class TestQueueLogLevelEnvVar:
    """`FARABUNKER_QUEUE_LOG_LEVEL` overrides the level for the two queue
    loggers above. Reload `config.settings` under a caller-supplied
    environment, same fixture shape `foundation/ops/tests/
    test_cookie_names.py` uses (and for the reason its own docstring
    gives): `importlib.reload` mutates the real module object in
    `sys.modules`, so it must be reloaded again, with the environment
    variable removed, once the test is done -- or every later test in
    this process would import `config.settings` and see this module's
    override."""

    @staticmethod
    def _reload(monkeypatch, value=None):
        import importlib

        import config.settings as shipped

        if value is None:
            monkeypatch.delenv(QUEUE_LOG_ENV, raising=False)
        else:
            monkeypatch.setenv(QUEUE_LOG_ENV, value)
        importlib.reload(shipped)
        return shipped

    def test_an_unset_environment_defaults_to_info(self, monkeypatch):
        shipped = self._reload(monkeypatch, None)
        try:
            assert shipped.LOGGING["loggers"]["models.queue"]["level"] == "INFO"
            assert shipped.LOGGING["loggers"]["models.contracts.engines"]["level"] == "INFO"
            assert shipped.LOGGING["loggers"]["models.registry"]["level"] == "INFO"
        finally:
            self._reload(monkeypatch, None)

    def test_a_valid_override_lowers_the_level(self, monkeypatch):
        shipped = self._reload(monkeypatch, "DEBUG")
        try:
            assert shipped.LOGGING["loggers"]["models.queue"]["level"] == "DEBUG"
            assert shipped.LOGGING["loggers"]["models.contracts.engines"]["level"] == "DEBUG"
            assert shipped.LOGGING["loggers"]["models.registry"]["level"] == "DEBUG"
        finally:
            self._reload(monkeypatch, None)

    def test_an_invalid_value_falls_back_to_info_rather_than_crashing_boot(self, monkeypatch):
        shipped = self._reload(monkeypatch, "not-a-real-level")
        try:
            assert shipped.LOGGING["loggers"]["models.queue"]["level"] == "INFO"
            assert shipped.LOGGING["loggers"]["models.contracts.engines"]["level"] == "INFO"
            assert shipped.LOGGING["loggers"]["models.registry"]["level"] == "INFO"
        finally:
            self._reload(monkeypatch, None)

    def test_an_explicitly_empty_value_falls_back_to_info(self, monkeypatch):
        shipped = self._reload(monkeypatch, "")
        try:
            assert shipped.LOGGING["loggers"]["models.queue"]["level"] == "INFO"
            assert shipped.LOGGING["loggers"]["models.contracts.engines"]["level"] == "INFO"
            assert shipped.LOGGING["loggers"]["models.registry"]["level"] == "INFO"
        finally:
            self._reload(monkeypatch, None)

    def test_case_is_normalised(self, monkeypatch):
        shipped = self._reload(monkeypatch, "warning")
        try:
            assert shipped.LOGGING["loggers"]["models.queue"]["level"] == "WARNING"
        finally:
            self._reload(monkeypatch, None)

    def test_the_running_suite_is_on_the_default(self):
        """Non-vacuous companion (same shape as `test_cookie_names.py`'s
        own): the module above is reloaded in isolation, so it could
        agree with itself while the suite this assertion runs inside had
        drifted."""
        assert django_settings.LOGGING["loggers"]["models.queue"]["level"] == "INFO"


class TestRealEmission:
    """The property a dict-shape assertion alone cannot prove: applied via
    `logging.config.dictConfig`, an INFO line from the worker's own logger
    name is actually captured, and a DEBUG line at the default level is
    not.

    Captured stderr via `capsys`, reading the same stream the `root`
    handler (`ext://sys.stderr`) is bound to -- proving the line was
    actually written, in the documented format, not merely that some
    in-process record object exists."""

    def test_an_info_line_from_the_worker_logger_is_captured_at_the_default_level(self, capsys):
        logging.config.dictConfig(django_settings.LOGGING)
        try:
            worker_logger = logging.getLogger("models.queue.worker")
            worker_logger.info("probe")
            worker_logger.debug("should not appear at the default level")
            captured = capsys.readouterr()
            assert "probe" in captured.err
            assert "should not appear at the default level" not in captured.err
            # And the format is the plain single-line one, not a bare
            # message: level and logger name are both in the line.
            assert "INFO models.queue.worker: probe" in captured.err
        finally:
            # Restore ordinary dictConfig-driven logging for the rest of
            # the session -- this test's dictConfig call is otherwise a
            # global, process-wide side effect.
            logging.config.dictConfig(django_settings.LOGGING)

    def test_an_engines_logger_is_captured_at_info_too(self, capsys):
        logging.config.dictConfig(django_settings.LOGGING)
        try:
            engine_logger = logging.getLogger("models.contracts.engines.ollama")
            engine_logger.info("engine probe")
            captured = capsys.readouterr()
            assert "INFO models.contracts.engines.ollama: engine probe" in captured.err
        finally:
            logging.config.dictConfig(django_settings.LOGGING)

    def test_a_registry_logger_is_captured_at_info_too(self, capsys):
        # The regression this whole namespace addition exists to fix:
        # `models.registry.bindings`'s mild footprint-dip branch logs at
        # INFO, and before `models.registry` was added here it inherited
        # the root's WARNING floor and never reached this handler at all.
        logging.config.dictConfig(django_settings.LOGGING)
        try:
            registry_logger = logging.getLogger("models.registry.bindings")
            registry_logger.info("registry probe")
            captured = capsys.readouterr()
            assert "INFO models.registry.bindings: registry probe" in captured.err
        finally:
            logging.config.dictConfig(django_settings.LOGGING)
