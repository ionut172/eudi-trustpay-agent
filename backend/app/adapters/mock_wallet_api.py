"""Small mock client representing an EU Wallet API response boundary."""

from app.domain.models import (
    IBANAttestationCredential,
    PIDCredential,
    WalletPresentationPayload,
)


class MockEUWalletApi:
    """Fetches synthetic wallet presentations for local development."""

    async def fetch_presentation(
        self,
        session_id: str,
        expected_name: str,
        target_iban: str,
        scenario: str = "valid",
    ) -> WalletPresentationPayload:
        scenario = scenario.strip().lower()

        if scenario == "mismatch":
            return WalletPresentationPayload(
                session_id=session_id,
                pid=PIDCredential(
                    sub="urn:eu:pid:NL-88888888",
                    given_name="Eve",
                    family_name="Malicious",
                    is_valid=True,
                ),
                iban_attestation=IBANAttestationCredential(
                    sub="urn:eu:pid:NL-99999999",
                    iban=target_iban,
                    account_holder_name="Bob Mule",
                    bank_name="ING Bank N.V.",
                    is_valid=True,
                    is_expired=False,
                ),
            )

        if scenario == "iban_mismatch":
            return WalletPresentationPayload(
                session_id=session_id,
                pid=PIDCredential(
                    sub="urn:eu:pid:RO-12345678",
                    given_name="Ionut",
                    family_name="Gabriel",
                    is_valid=True,
                ),
                iban_attestation=IBANAttestationCredential(
                    sub="urn:eu:pid:RO-12345678",
                    iban="RO23INGB999900001231",
                    account_holder_name="Ionut Gabriel",
                    bank_name="ING Bank N.V.",
                    is_valid=True,
                    is_expired=False,
                ),
            )

        if scenario == "missing_iban":
            return WalletPresentationPayload(
                session_id=session_id,
                pid=PIDCredential(
                    sub="urn:eu:pid:NL-12345678",
                    given_name=expected_name.split()[0] if expected_name else "Alice",
                    family_name=" ".join(expected_name.split()[1:]) if len(expected_name.split()) > 1 else "Visser",
                    is_valid=True,
                ),
                iban_attestation=None,
            )

        if scenario != "valid":
            raise ValueError("Unknown mock wallet scenario")

        names = expected_name.split()
        return WalletPresentationPayload(
            session_id=session_id,
            pid=PIDCredential(
                sub="urn:eu:pid:NL-12345678",
                given_name=names[0] if names else "Alice",
                family_name=" ".join(names[1:]) if len(names) > 1 else "Visser",
                is_valid=True,
            ),
            iban_attestation=IBANAttestationCredential(
                sub="urn:eu:pid:NL-12345678",
                iban=target_iban,
                account_holder_name=expected_name,
                bank_name="ING Bank N.V.",
                is_valid=True,
                is_expired=False,
            ),
        )
