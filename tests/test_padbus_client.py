"""Tests for the virtual gamepad bus client (``src/platform/padbus_client.py``).

Adopted from the Nimbus repository's protocol checks. Pure Python: no
driver, no hardware, and the module imports on any OS. The constants the
driver validates were confirmed on a real ViGEmBus (1.21.442.0, 2026-09-09)
by a probe script that lives in the Nimbus repository.

``X360Pad`` is driven over a fake bus handle that records IOCTLs; the real
bus is never opened (``tests/conftest.py::_no_real_pad_bus``).
"""

import ctypes
import importlib.util
import struct
import uuid

import pytest

from src.platform import padbus_client as pb

# Captured at import, which is during collection and so before the autouse
# guard patches them: a few tests need the real lookup logic, pointed at a
# GUID that no driver owns.
_REAL_FIND_BUS = pb.find_bus
_REAL_OPEN_BUS = pb.open_bus

_BOGUS = [("nothing", "{00000000-0000-0000-0000-000000000001}")]


class TestIoctlCodes:
    # CTL_CODE(FILE_DEVICE_BUS_EXTENDER, 0x801 + n, METHOD_BUFFERED, access)
    EXPECTED = {
        "IOCTL_VIGEM_PLUGIN_TARGET": 0x002AA004,
        "IOCTL_VIGEM_UNPLUG_TARGET": 0x002AA008,
        "IOCTL_VIGEM_CHECK_VERSION": 0x002AA00C,
        "IOCTL_VIGEM_WAIT_DEVICE_READY": 0x002AA010,
        "IOCTL_XUSB_REQUEST_NOTIFICATION": 0x002AE804,
        "IOCTL_XUSB_SUBMIT_REPORT": 0x002AA808,
        "IOCTL_XUSB_GET_USER_INDEX": 0x002AE81C,
    }

    @pytest.mark.parametrize("name", sorted(EXPECTED))
    def test_the_code_is_the_one_the_bus_dispatches_on(self, name):
        assert getattr(pb, name) == self.EXPECTED[name]

    def test_ctl_code_is_the_wdk_macro(self):
        assert pb.ctl_code(0x801, 2) == (0x2A << 16) | (2 << 14) | (0x801 << 2)

    def test_the_interface_version_is_one(self):
        assert pb.INTERFACE_VERSION == 0x0001


class TestStructureSizes:
    """The driver compares each request's ``Size`` against these."""

    @pytest.mark.parametrize(
        ("name", "size"),
        [
            ("VIGEM_CHECK_VERSION", 8),
            ("VIGEM_PLUGIN_TARGET", 16),
            ("VIGEM_UNPLUG_TARGET", 8),
            ("VIGEM_WAIT_DEVICE_READY", 8),
            ("XUSB_REPORT", 12),
            ("XUSB_SUBMIT_REPORT", 20),
            ("XUSB_REQUEST_NOTIFICATION", 12),
            ("XUSB_GET_USER_INDEX", 12),
        ],
    )
    def test_sizeof(self, name, size):
        assert ctypes.sizeof(getattr(pb, name)) == size


class TestPacking:
    def _report(self):
        return pb.XUSB_REPORT(
            wButtons=0x5010,
            bLeftTrigger=7,
            bRightTrigger=255,
            sThumbLX=-32768,
            sThumbLY=32767,
            sThumbRX=-1,
            sThumbRY=1234,
        )

    def test_the_report_packs_like_xinput_gamepad(self):
        packed = struct.pack("<HBBhhhh", 0x5010, 7, 255, -32768, 32767, -1, 1234)
        assert bytes(self._report()) == packed

    def test_a_submit_carries_the_report_after_size_and_serial(self):
        report = self._report()
        sub = pb.XUSB_SUBMIT_REPORT(Size=20, SerialNo=3)
        sub.Report = report
        assert bytes(sub) == struct.pack("<II", 20, 3) + bytes(report)

    def test_plugin_packs_xbox360_wired_with_microsofts_vid_pid(self):
        plug = pb.VIGEM_PLUGIN_TARGET(
            Size=16,
            SerialNo=1,
            TargetType=pb.TARGET_XBOX360_WIRED,
            VendorId=pb.XBOX360_VENDOR_ID,
            ProductId=pb.XBOX360_PRODUCT_ID,
        )
        assert bytes(plug) == struct.pack("<IIIHH", 16, 1, 0, 0x045E, 0x028E)


class TestFloatConversions:
    """vgamepad's conversions, so the shaping probe's expected values hold."""

    @pytest.mark.parametrize("x", [-1.0, -0.95, -0.5, -0.25, -0.1, 0.0, 0.1, 0.25, 0.5, 0.95, 1.0])
    def test_sticks_are_round_x_times_32767(self, x):
        state = pb.X360ReportState()
        state.left_joystick_float(x, -x)
        state.right_joystick_float(-x, x)
        r = state.report
        assert r.sThumbLX == round(x * 32767)
        assert r.sThumbLY == round(-x * 32767)
        assert r.sThumbRX == round(-x * 32767)
        assert r.sThumbRY == round(x * 32767)

    @pytest.mark.parametrize("t", [0.0, 0.1, 0.5, 0.95, 1.0])
    def test_triggers_are_round_t_times_255(self, t):
        state = pb.X360ReportState()
        state.left_trigger_float(t)
        state.right_trigger_float(1.0 - t)
        assert state.report.bLeftTrigger == round(t * 255)
        assert state.report.bRightTrigger == round((1.0 - t) * 255)

    def test_over_range_sticks_clamp_at_the_int16_limits(self):
        state = pb.X360ReportState()
        state.left_joystick_float(2.0, -2.0)
        assert state.report.sThumbLX == 32767
        assert state.report.sThumbLY == -32768

    def test_over_range_triggers_clamp_at_0_and_255(self):
        state = pb.X360ReportState()
        state.left_trigger_float(1.5)
        state.right_trigger_float(-0.5)
        assert state.report.bLeftTrigger == 255
        assert state.report.bRightTrigger == 0


class TestButtons:
    A = pb.XUSB_BUTTON.XUSB_GAMEPAD_A
    X = pb.XUSB_BUTTON.XUSB_GAMEPAD_X
    Y = pb.XUSB_BUTTON.XUSB_GAMEPAD_Y

    def test_reset_zeroes_the_report(self):
        state = pb.X360ReportState()
        state.left_trigger(9)
        state.press_button(self.A)
        state.reset()
        assert bytes(state.report) == bytes(12)

    def test_press_sets_only_its_bits(self):
        state = pb.X360ReportState()
        state.press_button(self.A)
        state.press_button(self.X)
        assert state.report.wButtons == 0x5000

    def test_release_clears_only_its_bit(self):
        state = pb.X360ReportState()
        state.press_button(self.A)
        state.press_button(self.X)
        state.release_button(self.A)
        assert state.report.wButtons == 0x4000

    def test_releasing_an_unpressed_button_changes_nothing(self):
        state = pb.X360ReportState()
        state.press_button(self.X)
        state.release_button(self.Y)
        assert state.report.wButtons == 0x4000

    def test_a_combined_mask_presses_both(self):
        state = pb.X360ReportState()
        state.press_button(self.X)
        state.press_button(self.A | self.Y)
        assert state.report.wButtons == 0xD000

    def test_the_masks_are_xinputs(self):
        masks = {
            "XUSB_GAMEPAD_DPAD_UP": 0x0001,
            "XUSB_GAMEPAD_DPAD_DOWN": 0x0002,
            "XUSB_GAMEPAD_DPAD_LEFT": 0x0004,
            "XUSB_GAMEPAD_DPAD_RIGHT": 0x0008,
            "XUSB_GAMEPAD_START": 0x0010,
            "XUSB_GAMEPAD_BACK": 0x0020,
            "XUSB_GAMEPAD_LEFT_THUMB": 0x0040,
            "XUSB_GAMEPAD_RIGHT_THUMB": 0x0080,
            "XUSB_GAMEPAD_LEFT_SHOULDER": 0x0100,
            "XUSB_GAMEPAD_RIGHT_SHOULDER": 0x0200,
            "XUSB_GAMEPAD_GUIDE": 0x0400,
            "XUSB_GAMEPAD_A": 0x1000,
            "XUSB_GAMEPAD_B": 0x2000,
            "XUSB_GAMEPAD_X": 0x4000,
            "XUSB_GAMEPAD_Y": 0x8000,
        }
        assert all(int(pb.XUSB_BUTTON[k]) == v for k, v in masks.items())

    def test_the_nimbus_button_ids_map_to_distinct_single_bits(self):
        """Nimbus's 14 button ids (A, B, X, Y, LB, RB, Back, Start, L3, R3,
        then the d-pad) resolve through this enum; the table lived in Nimbus's
        ``vigem_interface`` and is restated here as the contract."""
        by_id = {
            1: "XUSB_GAMEPAD_A",
            2: "XUSB_GAMEPAD_B",
            3: "XUSB_GAMEPAD_X",
            4: "XUSB_GAMEPAD_Y",
            5: "XUSB_GAMEPAD_LEFT_SHOULDER",
            6: "XUSB_GAMEPAD_RIGHT_SHOULDER",
            7: "XUSB_GAMEPAD_BACK",
            8: "XUSB_GAMEPAD_START",
            9: "XUSB_GAMEPAD_LEFT_THUMB",
            10: "XUSB_GAMEPAD_RIGHT_THUMB",
            11: "XUSB_GAMEPAD_DPAD_UP",
            12: "XUSB_GAMEPAD_DPAD_DOWN",
            13: "XUSB_GAMEPAD_DPAD_LEFT",
            14: "XUSB_GAMEPAD_DPAD_RIGHT",
        }
        expected = {
            1: 0x1000,
            2: 0x2000,
            3: 0x4000,
            4: 0x8000,
            5: 0x0100,
            6: 0x0200,
            7: 0x0020,
            8: 0x0010,
            9: 0x0040,
            10: 0x0080,
            11: 0x0001,
            12: 0x0002,
            13: 0x0004,
            14: 0x0008,
        }
        masks = {k: int(pb.XUSB_BUTTON[v]) for k, v in by_id.items()}
        assert masks == expected
        assert len(set(masks.values())) == 14
        assert all(bin(m).count("1") == 1 for m in masks.values())


class TestGuid:
    def test_from_string_packs_like_uuid_bytes_le(self):
        vigem = pb.BUS_INTERFACES[0][1]
        assert bytes(pb.GUID.from_string(vigem)) == uuid.UUID(vigem).bytes_le

    def test_vigembus_is_first_and_its_guid_is_the_published_one(self):
        name, guid = pb.BUS_INTERFACES[0]
        assert name == "ViGEmBus"
        assert guid.upper() == "{96E42B22-F5E9-42F8-B043-ED0F932F014F}"


class TestGracefulDegradation:
    def test_find_bus_on_an_absent_interface_is_none_without_raising(self):
        assert _REAL_FIND_BUS(_BOGUS) is None

    def test_interface_paths_on_an_absent_interface_is_empty(self):
        assert pb.interface_paths(_BOGUS[0][1]) == []

    def test_a_pad_on_an_absent_bus_raises_padbusnotfound(self, monkeypatch):
        monkeypatch.setattr(pb, "open_bus", _REAL_OPEN_BUS)
        with pytest.raises(pb.PadBusNotFound):
            pb.X360Pad(interfaces=_BOGUS)


class TestNoImportTimeEnumeration:
    def test_the_module_has_no_padbus_available_constant(self):
        assert not hasattr(pb, "PADBUS_AVAILABLE")

    def test_importing_the_module_enumerates_no_devices(self, monkeypatch):
        """Every enumeration builds a GUID (``uuid.UUID``) and a buffer, so make
        both raise while a fresh copy of the module executes. A fresh copy
        rather than ``importlib.reload``: a reload would rebind the functions
        the suite's pad-bus guard patched on the shared module."""

        def boom(*args, **kwargs):
            raise AssertionError("device enumeration at import time")

        monkeypatch.setattr(uuid, "UUID", boom)
        monkeypatch.setattr(ctypes, "create_unicode_buffer", boom)
        spec = importlib.util.spec_from_file_location("padbus_client_fresh", pb.__file__)
        assert spec is not None and spec.loader is not None
        fresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fresh)
        assert not hasattr(fresh, "PADBUS_AVAILABLE")


class TestTheSuiteNeverOpensTheRealBus:
    def test_a_pad_cannot_be_opened(self):
        with pytest.raises(pb.PadBusNotFound):
            pb.X360Pad()

    def test_open_bus_is_refused(self):
        with pytest.raises(pb.PadBusNotFound):
            pb.open_bus()

    def test_no_bus_is_present(self):
        assert pb.bus_present() is False


# ---- X360Pad over a fake bus ------------------------------------------------------------


class _FakeClock:
    """Stands in for the module's ``time``: sleeping advances monotonic time."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.001)


class _FakeBus:
    """A bus handle that records IOCTLs and fails them on demand."""

    name = "FakeBus"
    path = r"\\?\fake-bus"

    def __init__(self):
        self.calls = []  # (code, bytes of the request)
        self.closed = 0
        self.fail = lambda code, raw: None  # return an exception to raise it

    def ioctl(self, code, inbuf=None, outbuf=None, timeout_ms=None):
        raw = bytes(inbuf) if inbuf is not None else b""
        self.calls.append((code, raw))
        exc = self.fail(code, raw)
        if exc is not None:
            raise exc
        return 0

    def close(self):
        self.closed += 1

    def codes(self):
        return [c for c, _ in self.calls]

    def serial_of(self, raw):
        return struct.unpack_from("<II", raw)[1]


def _no_more_items(code=pb.ERROR_NO_MORE_ITEMS):
    return pb.PadBusError("no device object", code)


#: ViGEmBus 1.21 reports the first, 1.22 the second (STATUS_DEVICE_DOES_NOT_EXIST).
MISSING_CODES = [pb.ERROR_NO_MORE_ITEMS, pb.ERROR_DEV_NOT_EXIST]


@pytest.fixture
def bus(monkeypatch):
    fake = _FakeBus()
    monkeypatch.setattr(pb, "open_bus", lambda interfaces=pb.BUS_INTERFACES: fake)
    monkeypatch.setattr(pb, "present_child_serials", lambda: [])
    clock = _FakeClock()
    monkeypatch.setattr(pb, "time", clock)
    fake.clock = clock
    return fake


class TestPlugging:
    def test_a_new_pad_plugs_waits_for_ready_then_proves_it_with_a_report(self, bus):
        pad = pb.X360Pad()
        assert bus.codes() == [
            pb.IOCTL_VIGEM_PLUGIN_TARGET,
            pb.IOCTL_VIGEM_WAIT_DEVICE_READY,
            pb.IOCTL_XUSB_SUBMIT_REPORT,
        ]
        assert pad.serial == 1
        assert pad.plugged
        assert (pad.bus_name, pad.bus_path) == (bus.name, bus.path)

    def test_the_plugin_request_names_microsofts_xbox_360_pad(self, bus):
        pb.X360Pad()
        raw = bus.calls[0][1]
        assert raw == struct.pack("<IIIHH", 16, 1, 0, 0x045E, 0x028E)

    def test_serials_whose_child_is_still_present_are_skipped(self, bus, monkeypatch):
        monkeypatch.setattr(pb, "present_child_serials", lambda: [1, 2])
        pad = pb.X360Pad()
        assert pad.serial == 3
        assert pad.skipped_serials == [1, 2]
        assert bus.serial_of(bus.calls[0][1]) == 3

    def test_a_forced_serial_is_used_as_given(self, bus):
        assert pb.X360Pad(serial=7).serial == 7

    def test_a_bus_without_wait_device_ready_still_plugs(self, bus):
        def fail(code, raw):
            if code == pb.IOCTL_VIGEM_WAIT_DEVICE_READY:
                return pb.PadBusError("old bus", pb.ERROR_INVALID_FUNCTION)

        bus.fail = fail
        assert pb.X360Pad().serial == 1

    def test_a_serial_the_bus_refuses_moves_on_to_the_next(self, bus):
        def fail(code, raw):
            if code == pb.IOCTL_VIGEM_PLUGIN_TARGET and bus.serial_of(raw) == 1:
                return pb.PadBusError("taken", 5)

        bus.fail = fail
        assert pb.X360Pad().serial == 2

    def test_a_failed_plug_closes_the_bus_handle_and_raises(self, bus):
        bus.fail = lambda code, raw: (
            pb.PadBusError("refused", 5) if code == pb.IOCTL_VIGEM_PLUGIN_TARGET else None
        )
        with pytest.raises(pb.PadBusError):
            pb.X360Pad()
        assert bus.closed == 1

    @pytest.mark.parametrize("winerror", MISSING_CODES)
    def test_a_serial_that_never_takes_a_report_is_skipped_after_the_ready_timeout(
        self, bus, winerror
    ):
        def fail(code, raw):
            if code == pb.IOCTL_XUSB_SUBMIT_REPORT and bus.serial_of(raw) == 1:
                return _no_more_items(winerror)

        bus.fail = fail
        pad = pb.X360Pad(ready_timeout_ms=20)
        assert pad.serial == 2
        assert pad.skipped_serials == [1]
        assert pb.IOCTL_VIGEM_UNPLUG_TARGET in bus.codes()


class TestUpdate:
    def test_update_submits_the_state_set_through_the_setters(self, bus):
        pad = pb.X360Pad()
        pad.left_joystick_float(0.5, -0.5)
        pad.right_trigger_float(1.0)
        pad.press_button(pb.XUSB_BUTTON.XUSB_GAMEPAD_A)
        before = len(bus.calls)
        pad.update()
        code, raw = bus.calls[-1]
        assert len(bus.calls) == before + 1
        assert code == pb.IOCTL_XUSB_SUBMIT_REPORT
        expected = struct.pack(
            "<IIHBBhhhh",
            20,
            pad.serial,
            0x1000,
            0,
            255,
            round(0.5 * 32767),
            round(-0.5 * 32767),
            0,
            0,
        )
        assert raw == expected

    def test_update_on_an_unplugged_pad_raises(self, bus):
        pad = pb.X360Pad()
        pad.close()
        with pytest.raises(pb.PadBusError):
            pad.update()

    def test_the_missing_device_codes_are_259_and_55(self):
        assert pb.ERROR_DEV_NOT_EXIST == 55
        assert pb._is_missing_child(259) and pb._is_missing_child(55)
        assert not pb._is_missing_child(5)

    def test_another_error_while_proving_readiness_raises_without_retrying(self, bus):
        bus.fail = lambda code, raw: (
            pb.PadBusError("denied", 5) if code == pb.IOCTL_XUSB_SUBMIT_REPORT else None
        )
        with pytest.raises(pb.PadBusError):
            pb.X360Pad()
        submits = [c for c in bus.codes() if c == pb.IOCTL_XUSB_SUBMIT_REPORT]
        assert len(submits) == 1
        assert pb.IOCTL_VIGEM_UNPLUG_TARGET in bus.codes()

    @pytest.mark.parametrize("winerror", MISSING_CODES)
    def test_a_report_that_fails_once_is_retried_and_counted(self, bus, winerror):
        pad = pb.X360Pad()
        failures = [_no_more_items(winerror), _no_more_items(winerror)]
        bus.fail = lambda code, raw: failures.pop(0) if failures else None
        pad.update()
        assert pad.recovered_reports == 1
        assert pad.replugs == []

    def test_a_report_failing_for_another_reason_is_not_retried(self, bus):
        pad = pb.X360Pad()
        bus.fail = lambda code, raw: pb.PadBusError("denied", 5)
        before = len(bus.calls)
        with pytest.raises(pb.PadBusError):
            pad.update()
        assert len(bus.calls) == before + 1

    @pytest.mark.parametrize("winerror", MISSING_CODES)
    def test_a_pad_that_stays_gone_is_replugged_on_another_serial(self, bus, monkeypatch, winerror):
        monkeypatch.setattr(pb, "REPORT_RETRY_S", 0.02)
        pad = pb.X360Pad()
        pad.press_button(pb.XUSB_BUTTON.XUSB_GAMEPAD_B)
        assert pad.serial == 1

        def fail(code, raw):
            if code == pb.IOCTL_XUSB_SUBMIT_REPORT and bus.serial_of(raw) == 1:
                return _no_more_items(winerror)

        bus.fail = fail
        pad.update()
        assert pad.serial == 2
        assert pad.replugs == [(1, 2)]
        assert pad.recovered_reports == 0
        unplugs = [c for c in bus.codes() if c == pb.IOCTL_VIGEM_UNPLUG_TARGET]
        assert len(unplugs) == 1
        # The live state follows the pad to its new serial.
        code, raw = bus.calls[-1]
        assert code == pb.IOCTL_XUSB_SUBMIT_REPORT
        assert bus.serial_of(raw) == 2
        assert struct.unpack_from("<H", raw, 8)[0] == 0x2000


def _last_submit_buttons(bus):
    submits = [raw for code, raw in bus.calls if code == pb.IOCTL_XUSB_SUBMIT_REPORT]
    return struct.unpack_from("<H", submits[-1], 8)[0]


class TestCancel:
    def test_after_cancel_update_sends_an_all_zero_report(self, bus):
        pad = pb.X360Pad()
        pad.press_button(pb.XUSB_BUTTON.XUSB_GAMEPAD_A)
        pad.right_trigger_float(1.0)
        pad.left_joystick_float(1.0, 1.0)
        pad.cancel()
        pad.update()
        code, raw = bus.calls[-1]
        assert code == pb.IOCTL_XUSB_SUBMIT_REPORT
        assert raw == struct.pack("<IIHBBhhhh", 20, pad.serial, 0, 0, 0, 0, 0, 0, 0)

    def test_before_cancel_the_held_report_is_still_sent(self, bus):
        pad = pb.X360Pad()
        pad.press_button(pb.XUSB_BUTTON.XUSB_GAMEPAD_A)
        pad.update()
        assert _last_submit_buttons(bus) == 0x1000

    def test_a_replug_under_way_when_cancel_lands_does_not_resubmit_the_stale_report(
        self, bus, monkeypatch
    ):
        monkeypatch.setattr(pb, "REPORT_RETRY_S", 0.02)
        pad = pb.X360Pad()
        pad.press_button(pb.XUSB_BUTTON.XUSB_GAMEPAD_B)

        def fail(code, raw):
            if code == pb.IOCTL_XUSB_SUBMIT_REPORT and bus.serial_of(raw) == 1:
                return _no_more_items()
            # Cancel arrives while the new serial is being plugged.
            if code == pb.IOCTL_VIGEM_WAIT_DEVICE_READY:
                pad.cancel()

        bus.fail = fail
        pad.update()
        assert pad.serial == 2
        submits = [raw for code, raw in bus.calls if code == pb.IOCTL_XUSB_SUBMIT_REPORT]
        on_new_serial = [r for r in submits if bus.serial_of(r) == 2]
        assert on_new_serial
        assert all(struct.unpack_from("<H", r, 8)[0] == 0 for r in on_new_serial)

    def test_cancelled_pad_starts_no_new_replug(self, bus, monkeypatch):
        monkeypatch.setattr(pb, "REPORT_RETRY_S", 0.02)
        pad = pb.X360Pad()
        pad.cancel()
        bus.fail = lambda code, raw: (
            _no_more_items() if code == pb.IOCTL_XUSB_SUBMIT_REPORT else None
        )
        with pytest.raises(pb.PadBusError):
            pad.update()
        assert pad.replugs == []
        assert bus.codes().count(pb.IOCTL_VIGEM_PLUGIN_TARGET) == 1

    def test_cancel_twice_and_after_close_are_harmless(self, bus):
        pad = pb.X360Pad()
        pad.cancel()
        pad.cancel()
        pad.close()
        pad.cancel()
        assert bus.closed == 1


class TestClose:
    def test_close_unplugs_and_closes_the_handle_once(self, bus):
        pad = pb.X360Pad()
        pad.close()
        assert bus.codes().count(pb.IOCTL_VIGEM_UNPLUG_TARGET) == 1
        assert bus.closed == 1
        assert not pad.plugged

    def test_a_second_close_is_a_no_op(self, bus):
        pad = pb.X360Pad()
        pad.close()
        calls = len(bus.calls)
        pad.close()
        assert len(bus.calls) == calls
        assert bus.closed == 1

    def test_close_survives_a_failing_unplug(self, bus):
        pad = pb.X360Pad()
        bus.fail = lambda code, raw: pb.PadBusError("gone", 5)
        pad.close()
        assert bus.closed == 1

    def test_the_context_manager_closes(self, bus):
        with pb.X360Pad() as pad:
            assert pad.plugged
        assert bus.closed == 1
