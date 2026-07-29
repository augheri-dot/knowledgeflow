import os
from io import BytesIO
from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient

def test_azure_connection():
    print("============================================================")
    print("KnowledgeFlow: Azure Blob Storage Connection Verification")
    print("============================================================")

    # 1. Load environment variables
    load_dotenv()
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    container_name = os.getenv("AZURE_CONTAINER_NAME", "raw-eu-regulations")

    if not connection_string:
        print("[ERROR] AZURE_STORAGE_CONNECTION_STRING is missing in .env configuration.")
        return False

    try:
        # 2. Initialize BlobServiceClient
        print("[INFO] Connecting to Azure Storage Account...")
        blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        
        # 3. Verify Container Existence
        container_client = blob_service_client.get_container_client(container_name)
        if not container_client.exists():
            print(f"[ERROR] Target container '{container_name}' does not exist.")
            return False
        print(f"[INFO] Container '{container_name}' validated successfully.")

        # 4. Perform Streaming Upload Test
        test_filename = "connection_test.txt"
        test_content = b"KnowledgeFlow Azure Connection Verification Test - PASSED"
        dummy_stream = BytesIO(test_content)

        print(f"[INFO] Uploading verification payload: '{test_filename}'...")
        blob_client = container_client.get_blob_client(test_filename)
        blob_client.upload_blob(dummy_stream, overwrite=True)
        print("[INFO] Verification payload uploaded successfully.")

        # 5. Perform Download Test
        print("[INFO] Downloading payload for integrity check...")
        downloaded_bytes = blob_client.download_blob().readall()
        print(f"[INFO] Retrieved payload content: '{downloaded_bytes.decode('utf-8')}'")

        # 6. Cleanup Test Artifact
        print("[INFO] Cleaning up verification payload from Azure Container...")
        blob_client.delete_blob()
        print("[INFO] Cleanup operation completed.")

        print("============================================================")
        print("[SUCCESS] Azure Blob Storage connection is fully operational.")
        print("============================================================")
        return True

    except Exception as e:
        print("\n[FATAL] Azure connection test failed with exception:")
        print(f"Details: {str(e)}")
        print("============================================================")
        return False

if __name__ == "__main__":
    test_azure_connection()
