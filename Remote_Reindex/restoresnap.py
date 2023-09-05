import boto3
import requests
from requests_aws4auth import AWS4Auth
 
host = 'https://search-ossdomainname-t3rsxyqjv66354dum6wbloicfe.us-east-1.es.amazonaws.com/' #replace
region = 'us-east-1' #replace
service = 'es'
credentials = boto3.Session().get_credentials()
awsauth = AWS4Auth(credentials.access_key, credentials.secret_key, region, service, session_token=credentials.token)

path = '_snapshot/ossmanualsnapshotpath/snapshotfoldername/manualsnap/_restore' #replace
url = host + path

payload = {
 "indices": "-.kibana*,-.opendistro_security",
 "include_global_state": False
}

headers = {"Content-Type": "application/json"}

r = requests.post(url, auth=awsauth, json=payload, headers=headers)

print(r.text)
