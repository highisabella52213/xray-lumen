"""Lumen V32 protocol registry backed by the bundled Xray data plane."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import xray_manager

class TransportValidationError(ValueError): pass
class TransportUnavailableError(TransportValidationError): pass

def _safe_location_id(value: object) -> str:
    return "".join(c for c in str(value or "") if c.isalnum() or c in "-_")[:24]

@dataclass(frozen=True)
class TransportAdapter:
    id:str; label:str; short_label:str; description:str; scheme:str; network:str
    available:bool=True; unavailable_reason:str=""; default_alpn:str="http/1.1"
    def availability(self): return self.available,self.unavailable_reason
    def require_available(self):
        if not self.available: raise TransportUnavailableError(f"{self.label}: {self.unavailable_reason}")
    def validate_settings(self,value:object)->dict[str,Any]:
        if value is None:return {}
        if not isinstance(value,dict):raise TransportValidationError(f"{self.label} transport settings must be an object")
        if value:raise TransportValidationError(f"{self.label} does not accept client-defined transport settings")
        return {}
    def validate_security(self,value:object)->str:
        security=str(value if value is not None else 'tls').strip().lower()
        if security!='tls':raise TransportValidationError(f"{self.label} requires Railway edge TLS")
        return security
    def parameters(self,*,uuid:str,transport_host:str,location_id:object=None)->dict[str,str]:
        self.require_available();loc=_safe_location_id(location_id)
        return {"type":self.network,"host":transport_host,"path":xray_manager.public_path(self.id,uuid,loc)}
    def capability(self):
        ok,detail=self.availability();return {"id":self.id,"label":self.label,"short_label":self.short_label,"description":self.description,"available":ok,"status":"VERIFIED — XRAY RUNTIME" if ok else "NOT SUPPORTED BY CURRENT RUNTIME","detail":"" if ok else detail,"scheme":self.scheme,"network":self.network,"settings_schema":{"type":"object","additional_properties":False,"properties":{}}}

class TransportRegistry:
    def __init__(self,items):self._ordered=items;self._adapters={x.id:x for x in items}
    def get(self,value):
        key=str(value or '').strip();item=self._adapters.get(key)
        if not item:raise TransportValidationError(f"Unknown protocol/transport: {key or '(empty)'}")
        return item
    def validate(self,value,settings=None):
        item=self.get(value);item.require_available();return item.id,item.validate_settings(settings)
    def is_available(self,value):
        try:return self.get(value).availability()[0]
        except TransportValidationError:return False
    def validate_security(self,value,security=None):return self.get(value).validate_security(security)
    def vless_parameters(self,value,*,uuid,transport_host,location_id=None,settings=None):
        item=self.get(value);item.validate_settings(settings);return item.parameters(uuid=uuid,transport_host=transport_host,location_id=location_id)
    def capabilities(self):return [x.capability() for x in self._ordered]
    def endpoint(self,*args,**kwargs):return None
    def scheme(self,value):return self.get(value).scheme
    def network(self,value):return self.get(value).network

TRANSPORTS=TransportRegistry((
 TransportAdapter('vless-ws','VLESS / WebSocket','VLESS / WS','Xray VLESS over WebSocket.','vless','ws'),
 TransportAdapter('vless-xhttp','VLESS / XHTTP','VLESS / XHTTP','Xray VLESS over XHTTP.','vless','xhttp',default_alpn='h2,http/1.1'),
 TransportAdapter('vless-httpupgrade','VLESS / HTTPUpgrade','VLESS / HTTPUpgrade','Xray VLESS over HTTPUpgrade.','vless','httpupgrade'),
 TransportAdapter('vmess-ws','VMess / WebSocket','VMess / WS','Xray VMess over WebSocket.','vmess','ws'),
 TransportAdapter('trojan-ws','Trojan / WebSocket','Trojan / WS','Xray Trojan over WebSocket.','trojan','ws'),
 TransportAdapter('vless-tcp','VLESS / Raw TCP','VLESS / TCP','Railway HTTP edge does not expose raw TCP.','vless','tcp',False,'Raw TCP requires a separate Railway TCP proxy.'),
 TransportAdapter('vless-grpc','VLESS / gRPC','VLESS / gRPC','Unavailable behind this Railway HTTP edge.','vless','grpc',False,'Native HTTP/2 pass-through is not available.'),
 TransportAdapter('vless-kcp','VLESS / KCP','VLESS / KCP','Unavailable because Railway HTTP services do not expose UDP.','vless','kcp',False,'UDP/KCP is not available on the Railway HTTP edge.'),
))
