"""Unit tests for the queue dispatch seam (models/contracts/queue.py).

Pure settings + import_string resolution -- no DB, no worker, no console
code. Django settings are patched directly with `override_settings` per
test (inline, no conftest.py per repo convention). The job-kind registry is
snapshotted/restored the same way models/registry/tests/test_jobkinds.py
does (both build `reset_registry` from `models.contracts.testing.
registry_reset_fixture`, C-59), since `enqueue()` validates against it.
"""
from __future__ import annotations

import sys
import types

import pytest
from django.test import override_settings

from models.contracts import jobkinds, queue
from models.contracts.jobkinds import JobKind, register_job_kind
from models.registry.tests._helpers import registry_reset_fixture  # noqa: F401 -- re-exported

reset_registry = registry_reset_fixture(jobkinds, "_JOB_KINDS")


@pytest.fixture(autouse=True)
def register_rag_ask(reset_registry):
    register_job_kind(
        JobKind(
            key="rag.ask",
            label="RAG Ask",
            planner="tools.rag.jobs.plan_ask",
            handler="tools.rag.jobs.handle_ask",
            summarizer="tools.rag.jobs.summarize_ask",
        )
    )


def _install_fake_backend(monkeypatch, module_name, **attrs):
    """Install an in-memory module under `module_name` in `sys.modules` so
    `import_string`-based dispatch (fresh-per-call, no caching) can resolve
    it, mirroring how test_bindings.py patches `import_string` itself but
    exercising the real dotted-path lookup end to end."""
    module = types.ModuleType(module_name)
    for name, value in attrs.items():
        setattr(module, name, value)
    monkeypatch.setitem(sys.modules, module_name, module)
    return module


# --- enqueue: unknown kind never reaches the backend -----------------------


class TestEnqueueUnknownKind:
    def test_unknown_kind_raises_before_any_backend_import(self, monkeypatch):
        calls = []

        def sentinel_enqueue(kind, payload, *, priority=None):
            calls.append((kind, payload, priority))
            return 1

        _install_fake_backend(
            monkeypatch, "fake_backend_sentinel", enqueue=sentinel_enqueue
        )

        with override_settings(INFERENCE_QUEUE_BACKEND="fake_backend_sentinel"):
            with pytest.raises(ValueError, match="does.not.exist"):
                queue.enqueue("does.not.exist", {"a": 1})

        assert calls == []


# --- enqueue: valid kind dispatches to the configured backend --------------


class TestEnqueueDispatch:
    def test_dispatches_to_backend_named_in_settings(self, monkeypatch):
        calls = []

        def fake_enqueue(kind, payload, *, priority=None):
            calls.append((kind, payload, priority))
            return 42

        _install_fake_backend(monkeypatch, "fake_backend_dispatch", enqueue=fake_enqueue)

        with override_settings(INFERENCE_QUEUE_BACKEND="fake_backend_dispatch"):
            job_id = queue.enqueue("rag.ask", {"question": "hi"})

        assert job_id == 42
        assert calls == [("rag.ask", {"question": "hi"}, None)]

    def test_priority_is_passed_through(self, monkeypatch):
        calls = []

        def fake_enqueue(kind, payload, *, priority=None):
            calls.append((kind, payload, priority))
            return 7

        _install_fake_backend(monkeypatch, "fake_backend_priority", enqueue=fake_enqueue)

        with override_settings(INFERENCE_QUEUE_BACKEND="fake_backend_priority"):
            queue.enqueue("rag.ask", {"question": "hi"}, priority=5)

        assert calls == [("rag.ask", {"question": "hi"}, 5)]

    def test_backend_is_imported_fresh_on_every_call(self, monkeypatch):
        import_calls = []
        real_import_module = queue.import_module

        def counting_import_module(path):
            import_calls.append(path)
            return real_import_module(path)

        monkeypatch.setattr(queue, "import_module", counting_import_module)

        def fake_enqueue(kind, payload, *, priority=None):
            return 1

        _install_fake_backend(monkeypatch, "fake_backend_fresh", enqueue=fake_enqueue)

        with override_settings(INFERENCE_QUEUE_BACKEND="fake_backend_fresh"):
            queue.enqueue("rag.ask", {})
            queue.enqueue("rag.ask", {})

        assert import_calls == ["fake_backend_fresh", "fake_backend_fresh"]


# --- unset/blank backend setting -------------------------------------------


class TestBackendUnconfigured:
    @override_settings(INFERENCE_QUEUE_BACKEND="")
    def test_blank_setting_raises_clear_error_naming_it(self):
        with pytest.raises(ValueError, match="INFERENCE_QUEUE_BACKEND"):
            queue.enqueue("rag.ask", {})

    @override_settings(INFERENCE_QUEUE_BACKEND=None)
    def test_none_setting_raises_clear_error_naming_it(self):
        with pytest.raises(ValueError, match="INFERENCE_QUEUE_BACKEND"):
            queue.get_job(1)


# --- get_job passthrough ----------------------------------------------------


class TestGetJob:
    def test_dispatches_to_backend_and_returns_result_unchanged(self, monkeypatch):
        sentinel = object()

        def fake_get_job(job_id):
            assert job_id == 99
            return sentinel

        _install_fake_backend(monkeypatch, "fake_backend_getjob", get_job=fake_get_job)

        with override_settings(INFERENCE_QUEUE_BACKEND="fake_backend_getjob"):
            result = queue.get_job(99)

        assert result is sentinel


# --- QueueUnavailable: defined here, passed through untranslated -----------


class TestQueueUnavailablePassthrough:
    """T6: `QueueUnavailable` is defined in THIS module (not
    `models.queue.backend`, which only imports it) so a `tools/*` caller
    can catch `models.contracts.queue.QueueUnavailable` without ever
    importing `models.queue` -- see this module's docstring. `enqueue`/`get_job`
    are plain passthroughs: whatever the configured backend raises
    propagates unchanged, no wrapping/translation happens at this seam."""

    def test_enqueue_lets_a_backends_queue_unavailable_propagate_unchanged(self, monkeypatch):
        def fake_enqueue(kind, payload, *, priority=None):
            raise queue.QueueUnavailable("jobs tables unavailable")

        _install_fake_backend(monkeypatch, "fake_backend_unavailable_enqueue", enqueue=fake_enqueue)

        with override_settings(INFERENCE_QUEUE_BACKEND="fake_backend_unavailable_enqueue"):
            with pytest.raises(queue.QueueUnavailable):
                queue.enqueue("rag.ask", {})

    def test_get_job_lets_a_backends_queue_unavailable_propagate_unchanged(self, monkeypatch):
        def fake_get_job(job_id):
            raise queue.QueueUnavailable("jobs tables unavailable")

        _install_fake_backend(monkeypatch, "fake_backend_unavailable_getjob", get_job=fake_get_job)

        with override_settings(INFERENCE_QUEUE_BACKEND="fake_backend_unavailable_getjob"):
            with pytest.raises(queue.QueueUnavailable):
                queue.get_job(1)
