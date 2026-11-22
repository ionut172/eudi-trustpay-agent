"""Small mock client representing an EU Wallet API response boundary."""

import json
from pathlib import Path

from app.domain.models import (
    IBANAttestationCredential,
    PIDCredential,
    WalletPresentationPayload,
)


class MockEUWalletApi:
    """Loads synthetic wallet credentials from the local JSON fixture."""

    def __init__(self) -> None:
        fixture_path = Path(__file__).with_name("mock_wallets.json")
        with fixture_path.open("r", encoding="utf-8") as fixture_file:
            fixture = json.load(fixture_file)
        self._wallets = fixture["wallets"]

    def get_wallet(self, wallet_id: str) -> dict:
        wallet = self._wallets.get(wallet_id.strip().lower())
        if not wallet:
            raise ValueError(f"Unknown mock wallet '{wallet_id}'")
        return wallet

    def list_wallet_ids(self) -> list[str]:
        return sorted(self._wallets)

    def find_wallet_for_intent(self, expected_name: str, target_iban: str) -> dict:
        """Finds a fixture by both legal name and IBAN; names alone are ambiguous."""
        normalized_name = " ".join(expected_name.strip().lower().split())
        normalized_iban = "".join(target_iban.upper().split())

        for wallet in self._wallets.values():
            pid = wallet["pid"]
            full_name = " ".join(
                [pid["given_name"].strip(), pid["family_name"].strip()]
            ).lower()
            attestation = wallet["iban_attestation"]
            attested_iban = (attestation.get("iban") or "").replace(" ", "").upper()
            if full_name == normalized_name and attested_iban == normalized_iban:
                return wallet

        raise ValueError("No exact mock wallet matches name and IBAN")

    async def fetch_wallet_presentation(
        self,
        session_id: str,
        wallet_id: str,
        target_iban: str | None = None,
    ) -> WalletPresentationPayload:
        wallet = self.get_wallet(wallet_id)
        pid = PIDCredential.model_validate(wallet["pid"])

        attestation_data = dict(wallet["iban_attestation"])
        if attestation_data.get("iban") is None and target_iban:
            attestation_data["iban"] = target_iban

        return WalletPresentationPayload(
            session_id=session_id,
            pid=pid,
            iban_attestation=IBANAttestationCredential.model_validate(attestation_data),
        )

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
            wallet = self.get_wallet("ionut")
            pid = PIDCredential.model_validate(wallet["pid"])
            attestation = IBANAttestationCredential.model_validate(wallet["iban_attestation"])
            return WalletPresentationPayload(
                session_id=session_id,
                pid=pid,
                iban_attestation=attestation,
            )

        if scenario == "missing_iban":
            wallet_id = expected_name.strip().lower().split()[0]
            wallet = self.get_wallet(wallet_id)
            return WalletPresentationPayload(
                session_id=session_id,
                pid=PIDCredential.model_validate(wallet["pid"]),
                iban_attestation=None,
            )

        if scenario != "valid":
            raise ValueError("Unknown mock wallet scenario")

        wallet_id = expected_name.strip().lower().split()[0]
        try:
            wallet = self.find_wallet_for_intent(expected_name, target_iban)
            pid = PIDCredential.model_validate(wallet["pid"])
            attestation = IBANAttestationCredential.model_validate(wallet["iban_attestation"])
            return WalletPresentationPayload(
                session_id=session_id,
                pid=pid,
                iban_attestation=attestation,
            )
        except ValueError:
            return WalletPresentationPayload(
                session_id=session_id,
                pid=PIDCredential(
                    sub="urn:eu:pid:UNKNOWN-00000000",
                    given_name="Unknown",
                    family_name="Payee",
                    is_valid=True,
                ),
                iban_attestation=IBANAttestationCredential(
                    sub="urn:eu:pid:OTHER-00000000",
                    iban=target_iban,
                    account_holder_name="Unverified Account Holder",
                    bank_name="Unknown Bank",
                    is_valid=True,
                    is_expired=False,
                ),
            )
