"""Payments package: gateway abstraction (mock + Razorpay test mode)."""
from .gateway import MockGateway, RazorpayGateway, GatewayResult, get_gateway

__all__ = ["MockGateway", "RazorpayGateway", "GatewayResult", "get_gateway"]
