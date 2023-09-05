import boto3
import requests
from requests_aws4auth import AWS4Auth

host = 'https://search-ossdomainname-35ib24hlxbuz2swpmfvuvpmdge.us-east-1.es.amazonaws.com/' #replace
region = 'us-east-1' #replace
service = 'es'
credentials = boto3.Session().get_credentials()
awsauth = AWS4Auth(credentials.access_key, credentials.secret_key, region, service, session_token=credentials.token)


path = '_snapshot/ossmanualsnapshotpath' #replace
url = host + path

payload = {
  "type": "s3",
  "settings": {
    "bucket": "ossbucktname", #replace
    "region": "us-east-1", #replace
    "role_arn": "arn:aws:iam::123456789011:role/OpensearchSnapshotRole" #replace
  }
}

headers = {"Content-Type": "application/json"}

r = requests.put(url, auth=awsauth, json=payload, headers=headers)

print(r.status_code)
print(r.text)
