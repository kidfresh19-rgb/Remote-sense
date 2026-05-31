"""Authentication for the imagery access layer. OAuth2 token management lives here so it
is centralized in one place (CLAUDE.md invariant 1), shared by every real adapter, and
never scattered through callers."""

from rs_imagery.auth.cdse import CdseOAuth2Client

__all__ = ["CdseOAuth2Client"]
