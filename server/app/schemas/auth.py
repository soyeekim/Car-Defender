from app.schemas.base import CamelModel


class Agreements(CamelModel):
    terms_of_service: bool = False
    privacy: bool = False
    video_consent: bool = False


class SignupRequest(CamelModel):
    email: str
    password: str
    password_confirm: str
    agreements: Agreements


class LoginRequest(CamelModel):
    email: str
    password: str


class UserOut(CamelModel):
    id: str
    email: str
    onboarded_at: str | None
    is_demo: bool


class AuthResponse(CamelModel):
    user: UserOut
    access_token: str
    expires_in: int


class RefreshResponse(CamelModel):
    access_token: str
    expires_in: int


class AgreementOut(CamelModel):
    agreed: bool
    agreed_at: str | None


class MeResponse(CamelModel):
    id: str
    email: str
    onboarded_at: str | None
    is_demo: bool
    agreements: dict[str, AgreementOut]


class EmailAvailableResponse(CamelModel):
    available: bool
    reason: str | None


class PasswordResetRequest(CamelModel):
    email: str


class PasswordResetConfirmRequest(CamelModel):
    token: str
    password: str
    password_confirm: str


class MessageResponse(CamelModel):
    message: str


class OnboardingRequest(CamelModel):
    completed: bool = True
    skipped: bool = False


class OnboardingResponse(CamelModel):
    onboarded_at: str | None
