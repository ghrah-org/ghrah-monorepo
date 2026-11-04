from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from multiprocessing import Pipe

import cbor2
import pytest

from ghrah_kernel.payloads import PayloadUnavailable, decode_payload, encode_payload
from ghrah_kernel.protocol import Envelope, ProtocolError, decode_envelope, encode_envelope
from ghrah_kernel.wire import MAX_MESSAGE_BYTES, MessageConnection, MessageTooLarge


@dataclass
class Arguments:
    label: str
    nested: dict


def test_sdk_roundtrip_and_missing_payload_are_distinct_from_json_null():
    value = Arguments("消息", {"a": [1, True, None]})
    assert decode_payload(encode_payload(value)) == {"label": "消息", "nested": value.nested}
    assert decode_payload(encode_payload(None)) is None
    with pytest.raises(PayloadUnavailable):
        decode_payload(None)


def test_envelope_preserves_uninterpretable_business_bytes_and_can_strip_them():
    request = Envelope("request", 7, {"action": "echo"}, b"\xffnot JSON")
    copied = decode_envelope(encode_envelope(request))
    assert copied == request
    stripped = copied.without_payload()
    assert stripped.opaque_payload is None
    assert stripped.req_id == 7 and stripped.meta == {"action": "echo"}


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "request", "req_id": True},
        {"kind": "unknown", "req_id": 1},
        {"kind": "request", "req_id": 1, "opaque_payload": {"a": 1}},
        {"kind": "request", "req_id": 1, "profile": "wrong"},
    ],
)
def test_invalid_control_envelopes_are_rejected(fields):
    with pytest.raises(ProtocolError):
        decode_envelope(cbor2.dumps(fields))


def test_stdlib_message_boundaries_and_exact_limit():
    a, b = Pipe(duplex=True)
    sender, receiver = MessageConnection(a), MessageConnection(b)
    try:
        messages = [b"first", b"x" * MAX_MESSAGE_BYTES, b"last"]
        with ThreadPoolExecutor(max_workers=1) as executor:
            sent = executor.submit(lambda: [sender.send(message) for message in messages])
            assert [receiver.receive() for _ in messages] == messages
            sent.result(timeout=1)
    finally:
        a.close()
        b.close()


def test_oversize_send_is_rejected_before_writing_and_connection_survives():
    a, b = Pipe(duplex=True)
    sender, receiver = MessageConnection(a, 32), MessageConnection(b, 32)
    try:
        with pytest.raises(MessageTooLarge):
            sender.send(b"x" * 33)
        sender.send(b"healthy")
        assert receiver.receive() == b"healthy"
    finally:
        a.close()
        b.close()


def test_receive_limit_and_eof_are_delegated_to_stdlib():
    a, b = Pipe(duplex=True)
    try:
        a.send_bytes(b"x" * 33)
        with pytest.raises(OSError):
            MessageConnection(b, 32).receive()
    finally:
        a.close()
        b.close()
    a, b = Pipe(duplex=True)
    a.close()
    try:
        with pytest.raises(EOFError):
            MessageConnection(b).receive()
    finally:
        b.close()
