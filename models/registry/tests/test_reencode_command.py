"""Unit tests for models/registry/management/commands/reencode.py.

Tests patch `drift.run_rematerialize` at the bound name in the command module
(`models.registry.management.commands.reencode.drift.run_rematerialize`)
so the command's invocation logic is tested in isolation from drift's own
logic (which is covered by test_drift.py).

`@pytest.mark.django_db` is not used since the command doesn't directly
access the database -- `run_rematerialize` is mocked.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


class TestReencodeCommand:
    def test_invokes_run_rematerialize_with_role(self):
        """Command invokes drift.run_rematerialize with the specified role."""
        with patch(
            "models.registry.management.commands.reencode.drift.run_rematerialize",
            return_value={"reencoded": 42},
        ) as mock_rematerialize:
            call_command("reencode", "--role", "rag.embed")

        mock_rematerialize.assert_called_once_with("rag.embed")

    def test_uses_default_role_rag_embed(self):
        """Command uses 'rag.embed' as the default role."""
        with patch(
            "models.registry.management.commands.reencode.drift.run_rematerialize",
            return_value={"reencoded": 10},
        ) as mock_rematerialize:
            call_command("reencode")

        mock_rematerialize.assert_called_once_with("rag.embed")

    def test_translates_value_error_to_command_error(self):
        """Command converts ValueError from run_rematerialize into CommandError."""
        with patch(
            "models.registry.management.commands.reencode.drift.run_rematerialize",
            side_effect=ValueError("Role rag.missing has no rematerialize callback configured"),
        ):
            with pytest.raises(CommandError) as exc_info:
                call_command("reencode", "--role", "rag.missing")

        assert "no rematerialize callback" in str(exc_info.value)

    def test_translates_runtime_error_to_command_error(self):
        """A partial/failed re-encode (reencode_all's raise-on-partial
        RuntimeError) must exit non-zero as a CommandError, not print a
        SUCCESS tally."""
        with patch(
            "models.registry.management.commands.reencode.drift.run_rematerialize",
            side_effect=RuntimeError("reencode_all: only 1 of 3 prose documents re-encoded"),
        ):
            with pytest.raises(CommandError) as exc_info:
                call_command("reencode", "--role", "rag.embed")

        assert "only 1 of 3" in str(exc_info.value)

    def test_prints_dict_result_as_tally(self, capsys):
        """Command prints dictionary results as key=value pairs."""
        with patch(
            "models.registry.management.commands.reencode.drift.run_rematerialize",
            return_value={"documents": 100, "reencoded": 42},
        ):
            call_command("reencode", "--role", "rag.embed")

        captured = capsys.readouterr()
        assert "documents=100" in captured.out
        assert "reencoded=42" in captured.out

    def test_prints_scalar_result(self, capsys):
        """Command prints scalar results directly."""
        with patch(
            "models.registry.management.commands.reencode.drift.run_rematerialize",
            return_value=7,
        ):
            call_command("reencode", "--role", "rag.embed")

        captured = capsys.readouterr()
        assert "7" in captured.out
