import os
import json
from datetime import date, datetime
from json import JSONEncoder

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

prefix = os.environ["PREFIX"]
bucket = os.environ["BUCKET_NAME"]
role_name = os.environ['ROLENAME']
crawler = os.environ["CRAWLER_NAME"]
costonly = os.environ.get('COSTONLY').lower() == 'yes'

TEMP = "/tmp/data.json"

def lambda_handler(event, context):
    print(json.dumps(event))
    for r in event.get('Records', []):
        try:
            body = json.loads(r["body"])
            account_id = body["account_id"]
            account_name = body["account_name"]
            read_ta(account_id, account_name)
            upload_to_s3(prefix, account_id, body.get("payer_id"))
            start_crawler()
        except Exception as e:
            print(f"{type(e)}: {e}")

def upload_to_s3(prefix, account_id, payer_id):
    if os.path.getsize(TEMP) == 0:
        print(f"No data in file for {prefix}")
        return
    d = datetime.now()
    month = d.strftime("%m")
    year = d.strftime("%Y")
    _date = d.strftime("%d%m%Y-%H%M%S")
    path = f"optics-data-collector/{prefix}-data/payer_id={payer_id}/year={year}/month={month}/{DestinationPrefix}-{account_id}-{_date}.json"
    try:
        s3 = boto3.client("s3", config=Config(s3={"addressing_style": "path"}))
        s3.upload_file(TEMP, bucket, path )
        print(f"Data for {account_id} in s3 - {path}")
    except Exception as e:
        print(f"{type(e)}: {e}")

def assume_role(account_id, service, region):
    try:
        assumed = boto3.client('sts').assume_role(RoleArn=f"arn:aws:iam::{account_id}:role/{role_name}", RoleSessionName=prefix)
        creds = assumed['Credentials']
        return boto3.client(service, region_name=region,
            aws_access_key_id=creds['AccessKeyId'],
            aws_secret_access_key=creds['SecretAccessKey'],
            aws_session_token=creds['SessionToken'],
        )
    except ClientError as e:
        print(f"{account_id} {type(e)}: {e}")

def start_crawler():
    glue = boto3.client("glue")
    try:
        glue.start_crawler(Name=crawler)
    except Exception as e:
        if ('has already started' in str(e)):
            print('crawler already started')
            return
        print(f"{type(e)}: {e}")

class DateTimeEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()

def read_ta(account_id, account_name):
    f = open(TEMP, "w")
    support = assume_role(account_id, "support", "us-east-1")
    checks = support.describe_trusted_advisor_checks(language="en")["checks"]
    for check in checks:
        #print(json.dumps(check))
        if (costonly and check.get("category") != "cost_optimizing"): continue
        try:
            result = support.describe_trusted_advisor_check_result(checkId=check["id"], language="en")['result']
            #print(json.dumps(result))
            if result.get("status") == "not_available": continue
            dt = result['timestamp']
            ts = datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ').strftime('%s')
            for resource in result["flaggedResources"]:
                output = {}
                if "metadata" in resource:
                    output.update(dict(zip(check["metadata"], resource["metadata"])))
                    del resource['metadata']
                resource["Region"] = resource.pop("region") if "region" in resource else '-'
                resource["Status"] = resource.pop("status") if "status" in resource else '-'
                output.update({"AccountId":account_id, "AccountName":account_name, "Category": check["category"], 'DateTime': dt, 'Timestamp': ts, "CheckName": check["name"], "CheckId": check["id"]})
                output.update(resource)
                f.write(json.dumps(output, cls=DateTimeEncoder) + "\n")
            else:
                pass
        except Exception as e:
            print(f'{type(e)}: {e}')
