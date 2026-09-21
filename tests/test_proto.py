from google.protobuf.message import Message

from planet_charlu.generated import bazaar_pb2


def test_generated_protocol_module_imports() -> None:
    assert bazaar_pb2 is not None


def test_generated_module_contains_messages() -> None:
    message_classes = [
        value
        for value in vars(bazaar_pb2).values()
        if isinstance(value, type) and issubclass(value, Message)
    ]

    assert message_classes, "No generated Protobuf message classes were found"