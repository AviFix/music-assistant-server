import aiohttp
import time
import logging

GOOGLE_CLIENT_ID = "1033576174882-e4vck7ppgp1plvc095v9k0u8mdsp7s30.apps.googleusercontent.com"
FIREBASE_API_KEY = "AIzaSyByyRIbRvi6MLOjfWqdv73B88x2QsVkOZA"
GOOGLE_OAUTH_URL = (
    "https://accounts.google.com/o/oauth2/v2/auth"
    "?client_id={client_id}"
    "&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fuserinfo.profile%20https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fuserinfo.email"
    "&redirect_uri={redirect_uri}"
    "&prompt=select_account"
    "&response_type=token"
    "&include_granted_scopes=true"
    "&enable_granular_consent=true"
    "&service=lso"
    "&o2v=2"
    "&flowName=GeneralOAuthFlow"
)

logger = logging.getLogger("music_assistant.providers.zing.auth")

class ZingAuthHelper:
    @staticmethod
    def get_oauth_url(redirect_uri: str) -> str:
        return GOOGLE_OAUTH_URL.format(client_id=GOOGLE_CLIENT_ID, redirect_uri=redirect_uri)

    @staticmethod
    async def exchange_google_token_for_firebase(google_access_token: str) -> dict:
        """Exchange Google access token for Firebase ID token and refresh token."""
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp?key={FIREBASE_API_KEY}"
        payload = {
            "requestUri": "http://localhost",
            "returnSecureToken": True,
            "postBody": f"&access_token={google_access_token}&providerId=google.com"
        }
        headers = {"Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                return await resp.json()

    @staticmethod
    async def refresh_firebase_token(refresh_token: str) -> dict:
        url = f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_API_KEY}"
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        logger.info("Requesting new access token using refresh token...")
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, headers=headers) as resp:
                resp.raise_for_status()
                result = await resp.json()
                logger.info(f"Received token response: {result}")
                return result

    @staticmethod
    async def login_with_refresh_token(refresh_token: str) -> dict:
        """Exchange a refresh token for a new access token and return the token response dict."""
        logger.info("Starting login with refresh token...")
        if not refresh_token:
            logger.error("No refresh token provided.")
            raise Exception("No refresh token provided.")
        data = await ZingAuthHelper.refresh_firebase_token(refresh_token)
        if data and "id_token" in data and "refresh_token" in data and "expires_in" in data:
            logger.info("Login successful. Token response received.")
            return data
        else:
            logger.error(f"Failed to login: {data}")
            raise Exception("Failed to login and obtain access token.")

    @staticmethod
    def get_user_id_from_token_response(data: dict) -> str | None:
        """Extract the user_id from a Firebase token response dict."""
        return data.get("user_id") or data.get("localId") 