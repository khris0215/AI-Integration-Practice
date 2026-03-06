import msal
import os
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
TENANT_ID = os.getenv("TENANT_ID")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["https://graph.microsoft.com/Files.Read"]

def get_token():
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise Exception("Failed to create device flow")
    print(flow["message"])  # Instructs user to visit https://microsoft.com/devicelogin
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" in result:
        return result["access_token"]
    else:
        raise Exception(result.get("error_description"))