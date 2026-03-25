"""
Evolution Facade / Factory 테스트.

- ENABLE_GUARDIAN_APIS=0 → status=DISABLED, code=AIRGAP_BLOCK
- ENABLE_GUARDIAN_APIS=1, ENABLE_EVOLUTION_ADAPTER=0 → status=DISABLED, code=ADAPTER_DISABLED
- 트리거 OFF 시 tick 호출 → TRIGGER_DISABLED (수동 cycle은 adapter ON이면 가능)
- DisabledEvolutionService: reject/list/apply 모두 EvolutionResponse(status=DISABLED) 반환
- reset_evolution_service_cache() fixture 사용
"""
import asyncio
import os
import unittest

from mellow_link.config.settings import clear_settings_cache, get_settings
from mellow_link.core.evolution_factory import reset_evolution_service_cache


def _env_set(key: str, val: str) -> None:
    os.environ[key] = val


def _env_unset(key: str) -> None:
    os.environ.pop(key, None)


def _reset_evolution_and_settings():
    """테스트 fixture: 설정 캐시 + Evolution 서비스 캐시 리셋."""
    clear_settings_cache()
    reset_evolution_service_cache()


class TestEvolutionFacadeAirgapBlock(unittest.TestCase):
    """ENABLE_GUARDIAN_APIS=0 이면 get_evolution_service().run_cycle() → DISABLED + AIRGAP_BLOCK."""

    def setUp(self):
        _env_set("ENABLE_GUARDIAN_APIS", "0")
        _env_set("ENABLE_EVOLUTION_ADAPTER", "0")
        _reset_evolution_and_settings()

    def tearDown(self):
        _env_unset("ENABLE_GUARDIAN_APIS")
        _env_unset("ENABLE_EVOLUTION_ADAPTER")
        clear_settings_cache()
        reset_evolution_service_cache()

    def test_run_cycle_returns_disabled_airgap_block(self):
        from mellow_link.core.evolution_factory import get_evolution_service
        svc = get_evolution_service()
        self.assertFalse(get_settings().allow_guardian_api())
        resp = asyncio.run(svc.run_cycle("test request"))
        self.assertEqual(resp.status, "DISABLED", resp)
        self.assertIsNotNone(resp.disabled_reason)
        self.assertEqual(resp.disabled_reason.code, "AIRGAP_BLOCK")
        self.assertIn("ENABLE_GUARDIAN_APIS", resp.disabled_reason.message)


class TestEvolutionFacadeAdapterDisabled(unittest.TestCase):
    """ENABLE_GUARDIAN_APIS=1, ENABLE_EVOLUTION_ADAPTER=0 이면 DISABLED + ADAPTER_DISABLED."""

    def setUp(self):
        _env_set("ENABLE_GUARDIAN_APIS", "1")
        _env_set("ENABLE_EVOLUTION_ADAPTER", "0")
        _reset_evolution_and_settings()

    def tearDown(self):
        _env_unset("ENABLE_GUARDIAN_APIS")
        _env_unset("ENABLE_EVOLUTION_ADAPTER")
        clear_settings_cache()
        reset_evolution_service_cache()

    def test_run_cycle_returns_disabled_adapter_disabled(self):
        from mellow_link.core.evolution_factory import get_evolution_service
        svc = get_evolution_service()
        self.assertTrue(get_settings().allow_guardian_api())
        resp = asyncio.run(svc.run_cycle("test request"))
        self.assertEqual(resp.status, "DISABLED")
        self.assertIsNotNone(resp.disabled_reason)
        self.assertEqual(resp.disabled_reason.code, "ADAPTER_DISABLED")
        self.assertIn("ENABLE_EVOLUTION_ADAPTER", resp.disabled_reason.message or "")


class TestEvolutionTriggerDisabled(unittest.TestCase):
    """트리거 OFF 상태에서 run_evolution_tick() 호출 시 TRIGGER_DISABLED 반환. 수동 cycle은 막지 않음."""

    def setUp(self):
        _env_unset("ENABLE_EVOLUTION_TRIGGER")
        _env_set("ENABLE_EVOLUTION_TRIGGER", "0")
        clear_settings_cache()

    def tearDown(self):
        _env_unset("ENABLE_EVOLUTION_TRIGGER")
        clear_settings_cache()

    def test_tick_returns_trigger_disabled_when_trigger_off(self):
        from mellow_link.core.evolution_trigger import run_evolution_tick, is_evolution_trigger_enabled
        self.assertFalse(is_evolution_trigger_enabled())
        success, payload, msg = asyncio.run(run_evolution_tick())
        self.assertFalse(success)
        self.assertIn("TRIGGER_DISABLED", msg)


class TestEvolutionTriggerGateBeforeTower(unittest.TestCase):
    """게이트 판정이 Tower 호출 전에 이루어짐. ENABLE_GUARDIAN_APIS=0 이면 tick에서 즉시 AIRGAP_BLOCK 반환."""

    def setUp(self):
        _env_set("ENABLE_GUARDIAN_APIS", "0")
        _env_set("ENABLE_EVOLUTION_ADAPTER", "0")
        _reset_evolution_and_settings()

    def tearDown(self):
        _env_unset("ENABLE_GUARDIAN_APIS")
        _env_unset("ENABLE_EVOLUTION_ADAPTER")
        clear_settings_cache()
        reset_evolution_service_cache()

    def test_tick_returns_airgap_block_before_tower(self):
        import unittest.mock as mock
        from mellow_link.core.evolution_trigger import run_evolution_tick
        # 트리거는 켜져 있다고 가정하여, 게이트 체크 구간으로 진입 (Tower 호출 전 반환)
        with mock.patch("mellow_link.core.evolution_trigger.is_evolution_trigger_enabled", return_value=True):
            success, payload, msg = asyncio.run(run_evolution_tick())
        self.assertFalse(success)
        self.assertIn("ENABLE_GUARDIAN_APIS", msg)
        if isinstance(payload, dict):
            self.assertEqual(payload.get("status"), "DISABLED")
            dr = payload.get("disabled_reason") or {}
            self.assertEqual(dr.get("code"), "AIRGAP_BLOCK")


class TestDisabledEvolutionServiceResponseSchema(unittest.TestCase):
    """DisabledEvolutionService의 reject/list/apply 등이 false/empty가 아닌 EvolutionResponse(DISABLED) 반환."""

    def setUp(self):
        _env_set("ENABLE_GUARDIAN_APIS", "0")
        _env_set("ENABLE_EVOLUTION_ADAPTER", "0")
        _reset_evolution_and_settings()

    def tearDown(self):
        _env_unset("ENABLE_GUARDIAN_APIS")
        _env_unset("ENABLE_EVOLUTION_ADAPTER")
        clear_settings_cache()
        reset_evolution_service_cache()

    def test_reject_proposal_returns_disabled_response(self):
        from mellow_link.core.evolution_factory import get_evolution_service
        svc = get_evolution_service()
        resp = svc.reject_proposal("dummy-id")
        self.assertEqual(resp.status, "DISABLED")
        self.assertIsNotNone(resp.disabled_reason)
        self.assertIn(resp.disabled_reason.code, ("AIRGAP_BLOCK", "ADAPTER_DISABLED"))
        self.assertFalse(resp.success if resp.success is not None else True)

    def test_list_waiting_returns_disabled_response_with_items(self):
        from mellow_link.core.evolution_factory import get_evolution_service
        svc = get_evolution_service()
        resp = svc.list_waiting_for_approval()
        self.assertEqual(resp.status, "DISABLED")
        self.assertIsNotNone(resp.disabled_reason)
        self.assertIsInstance(resp.items, list)
        self.assertEqual(len(resp.items), 0)

    def test_apply_from_proposal_returns_disabled_response(self):
        from mellow_link.core.evolution_factory import get_evolution_service
        svc = get_evolution_service()
        resp = svc.apply_from_proposal("dummy-id")
        self.assertEqual(resp.status, "DISABLED")
        self.assertIsNotNone(resp.disabled_reason)
        self.assertIs(resp.apply_ok, False)
        self.assertIsNotNone(resp.apply_message)
