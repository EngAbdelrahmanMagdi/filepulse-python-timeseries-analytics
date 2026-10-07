import socket

from app.core.models import FileEvent


class UdpSender:
    def __init__(self, host: str, port: int, max_payload: int = 8192) -> None:
        self.address = (host, port)
        self.max_payload = max_payload
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, event: FileEvent) -> None:
        payload = event.model_dump_json().encode("utf-8")
        if len(payload) > self.max_payload:
            raise ValueError("event exceeds UDP payload limit")
        self.socket.sendto(payload, self.address)

    def close(self) -> None:
        self.socket.close()
