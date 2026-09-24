import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.auth import create_access_token, decode_access_token
from backend.main import current_user


class AuthTokenTests(unittest.TestCase):
    def setUp(self):
        self.original_secret = os.environ.get("INVENTORY_SECRET_KEY")
        os.environ["INVENTORY_SECRET_KEY"] = "test-secret-key-with-at-least-32-characters"

    def tearDown(self):
        if self.original_secret is None:
            os.environ.pop("INVENTORY_SECRET_KEY", None)
        else:
            os.environ["INVENTORY_SECRET_KEY"] = self.original_secret

    def test_token_contains_authorization_version(self):
        token = create_access_token({"id": 7, "role": "worker", "auth_version": 3})

        self.assertEqual(decode_access_token(token)["ver"], 3)

    def test_old_token_is_rejected_after_authorization_version_changes(self):
        user = {"id": 7, "role": "worker", "auth_version": 1}
        token = create_access_token(user)

        with patch("backend.main.get_user_for_auth", return_value={**user, "auth_version": 2}):
            with self.assertRaises(HTTPException) as caught:
                current_user(f"Bearer {token}")

        self.assertEqual(caught.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
