"""Payment request schemas (DC-11, DC-10): only the three ids the gateway's checkout hands back;
no amount and nothing card-related is accepted."""

from pydantic import BaseModel, ConfigDict, Field


class PaymentConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    razorpay_order_id: str = Field(pattern=r"^order_[A-Za-z0-9]{1,34}$")
    razorpay_payment_id: str = Field(pattern=r"^pay_[A-Za-z0-9]{1,36}$")
    razorpay_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
