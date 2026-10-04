# Use a managed authentication provider

Authentication (passwords, sessions, reset, MFA, social login) is delegated to a managed provider with EU data residency; FastAPI only verifies the JWTs it issues. Hand-rolled auth is the highest-risk code to own in a finance app. The provider is lock-in that would take real effort to swap, hence this record. The specific provider is still to be chosen.
