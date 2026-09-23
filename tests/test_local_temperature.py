"""`[[agent]] temperature` for local seats.

The local adapter always sent the greedy `temperature: 0`. Greedy is a good
default for a reviewer, but gpt-oss does not survive it: replaying a real jury
prompt at 0, its reasoning looped ("Ok." x164) until the 8,192-token output cap
and it never answered; the same prompt at 1 produced a review. These tests pin
the knob: the default stays byte-identical, a configured value reaches the wire,
and a bad value is refused before the panel starts.

Network-free.
"""

from __future__ import annotations

import json
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.adapters import LocalAdapter  # noqa: E402
from ai_jury.config import (  # noqa: E402
    KNOWN_AGENT_KEYS,
    AgentSpec,
    ConfigError,
    _from_dict,
    config_hash,
    is_valid_temperature,
    validate_config,
)


def _spec(**kw):
    base = {"name": "local", "vendor": "local", "model": "gpt-oss:20b"}
    base.update(kw)
    return AgentSpec(**base)


def _data(temperature=None, vendor="local", **agent_over):
    agent = {"name": "a", "vendor": vendor, "model": "m"}
    if vendor not in ("local", "openai-api"):
        agent["command"] = "x"
    if temperature is not None:
        agent["temperature"] = temperature
    agent.update(agent_over)
    return {"jury": {"rounds": 1, "chair": "a"}, "agent": [agent]}


class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *_args):
        return self._payload


class PayloadTest(unittest.TestCase):
    def test_unset_sends_the_literal_zero_every_release_sent(self):
        body = LocalAdapter(_spec()).build_payload("p")
        self.assertEqual(body["temperature"], 0)
        self.assertIsInstance(body["temperature"], int)
        self.assertEqual(json.dumps(body["temperature"]), "0")

    def test_configured_value_is_sent(self):
        self.assertEqual(
            LocalAdapter(_spec(temperature=1.0)).build_payload("p")["temperature"], 1.0
        )
        self.assertEqual(
            LocalAdapter(_spec(temperature=0.7)).build_payload("p")["temperature"], 0.7
        )

    def test_explicit_zero_is_sent_as_zero(self):
        self.assertEqual(
            LocalAdapter(_spec(temperature=0.0)).build_payload("p")["temperature"], 0.0
        )

    def test_payload_carries_no_other_new_field(self):
        body = LocalAdapter(_spec(temperature=1.0)).build_payload("p")
        self.assertEqual(sorted(body), ["messages", "model", "stream", "temperature"])

    def test_value_reaches_the_wire(self):
        # Not just build_payload: the bytes run() actually posts.
        sent = {}

        def fake_open(req, _timeout):
            sent["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResp(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode())

        with mock.patch("ai_jury.adapters._open", side_effect=fake_open):
            result = LocalAdapter(_spec(temperature=1.0)).run("prompt")
        self.assertTrue(result.ok)
        self.assertEqual(sent["body"]["temperature"], 1.0)


class ValidationTest(unittest.TestCase):
    def test_known_agent_key(self):
        self.assertIn("temperature", KNOWN_AGENT_KEYS)
        # Accepted without an "unknown key" warning, even under --strict-config.
        self.assertEqual(validate_config(_data(1.0), strict=True), [])

    def test_in_range_values_are_accepted(self):
        for value in (0, 0.0, 0.2, 1, 1.0, 2, 2.0):
            self.assertEqual(validate_config(_data(value)), [], value)

    def test_out_of_range_is_a_hard_error(self):
        for value in (-0.1, 2.01, 5, float("inf"), float("-inf"), float("nan")):
            with self.assertRaises(ConfigError, msg=value) as ctx:
                validate_config(_data(value))
            self.assertIn("temperature must be a number from 0 to 2", str(ctx.exception))

    def test_non_numeric_is_a_hard_error(self):
        for value in ("1", "high", True, False, [1.0], {"t": 1}):
            with self.assertRaises(ConfigError, msg=repr(value)):
                validate_config(_data(value))

    def test_non_local_seat_warns_and_ignores(self):
        warnings = validate_config(_data(1.0, vendor="openai-api"))
        self.assertEqual(len(warnings), 1)
        self.assertIn("applies only to local seats", warnings[0])

    def test_adapter_decides_not_vendor(self):
        # A seat whose identity is another vendor but whose protocol is `local`
        # does send the temperature, so it must not be warned about.
        self.assertEqual(validate_config(_data(1.0, vendor="openai", adapter="local")), [])

    def test_absent_key_is_silent(self):
        self.assertEqual(validate_config(_data()), [])

    def test_predicate(self):
        self.assertTrue(is_valid_temperature(1))
        self.assertTrue(is_valid_temperature(0.5))
        self.assertFalse(is_valid_temperature(True))
        self.assertFalse(is_valid_temperature("1"))
        self.assertFalse(is_valid_temperature(None))


class ParseAndHashTest(unittest.TestCase):
    def test_parsed_as_float(self):
        cfg = _from_dict(_data(1))
        self.assertEqual(cfg.agents[0].temperature, 1.0)
        self.assertIsInstance(cfg.agents[0].temperature, float)

    def test_absent_stays_none(self):
        self.assertIsNone(_from_dict(_data()).agents[0].temperature)

    def test_invalid_reads_as_unset_in_the_parser(self):
        # validate_config refuses it; the parser alone must not carry it.
        self.assertIsNone(_from_dict(_data("hot")).agents[0].temperature)

    def test_set_temperature_splits_the_cache_key(self):
        # At 0 gpt-oss answers nothing and at 1 it reviews: not the same run.
        unset = config_hash(_from_dict(_data()))
        self.assertNotEqual(unset, config_hash(_from_dict(_data(1.0))))
        self.assertNotEqual(
            config_hash(_from_dict(_data(0.5))), config_hash(_from_dict(_data(1.0)))
        )

    def test_unset_leaves_the_cache_key_unchanged(self):
        # The canonical payload of a config that never names the key must not
        # gain one, so every existing cache entry stays valid.
        captured = []
        real_dumps = json.dumps

        def spy(obj, *a, **kw):
            captured.append(obj)
            return real_dumps(obj, *a, **kw)

        with mock.patch("json.dumps", side_effect=spy):
            config_hash(_from_dict(_data()))
        agent = captured[-1]["agents"][0]
        self.assertNotIn("temperature", agent)


if __name__ == "__main__":
    unittest.main()
