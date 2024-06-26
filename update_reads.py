import boto3
import pymongo
from dotenv import load_dotenv
from os import getenv
from utils.db import get_mongo_client
from pymongo.errors import BulkWriteError
from pprint import pprint
from collections import defaultdict
import pandas as pd
import re
from itertools import chain, combinations
from thefuzz import fuzz
from collections import namedtuple
import logging
import numpy as np
import sys
import math

#DOESN'T OVERRIDE EXISTING FILES! 

logging.basicConfig(format="%(levelname)s:%(message)s", level=logging.INFO)

# Change CCGP-project on line 182.
def list_s3_bucket_objs():
    """Returns s3 objectCollection"""
    load_dotenv()
    s3 = boto3.resource(
        "s3",
        aws_access_key_id=getenv("aws_access_key_id"),
        aws_secret_access_key=getenv("aws_secret_access_key"),
        endpoint_url=getenv("endpoint_url"),
    )
    bucket = s3.Bucket("ccgp")

    return bucket.objects.all()


def update_db_all(db_client: pymongo.MongoClient, files):
    """Updates db with list of files."""
    db = db_client["ccgp_dev"]
    collection = db["reads"]

    operations = []
    for file in files:
        operations.append(
            pymongo.operations.UpdateOne(  # type: ignore
                filter={"file_name": file.key},
                update={
                    "$setOnInsert": {
                        "filesize": file.size,
                        "mdate": file.last_modified,
                    }
                },  # Since upsert is true, we dont need to set file_name explicitly.
                upsert=True,
            )
        )
    try:
        collection.bulk_write(operations)

    except BulkWriteError as bwe:
        print(bwe.details)


def search(sample: dict, files: list[dict]):
    """
    Searches query file name in list of files. We search for the name followed by any of: ["_", "-", "."].
    Returns a tuple of the matched sample, bool if pref_id was used for matching, and the found files.
    """


    def find_files(q: str) -> list[dict]:
        q = str(q)

        if q is None or q.lower() == "nan":
            return False
        
        ### NEW - Handles minicore seq's separated by a comma. ###
        minicore_ids = q.split(',')
        found_files = []

        for minicore_id in minicore_ids:
            found = [
                file
                for file in files
                if f"{minicore_id}_" in file["file_name"]
                or f"{minicore_id}-" in file["file_name"]
                or f"{minicore_id}." in file["file_name"]
            ]
            found_files.extend(found)
            if found_files:
                return found_files
            elif "_" in minicore_id:
                
                q = minicore_id.replace("_", "-")
                found = [
                    file
                    for file in files
                    if f"{q}_" in file["file_name"]
                    or f"{q}-" in file["file_name"]
                    or f"{q}." in file["file_name"]
                ]
                found_files.extend(found)
                if found_files:
                    return found_files
                else:
                    q = minicore_id.replace("-", "")
                    
                    found = [
                        file
                        for file in files
                        if f"{q}_" in file["file_name"]
                        or f"{q}-" in file["file_name"]
                        or f"{q}." in file["file_name"]
                    ]
                    found_files.extend(found)
                    if found_files:
                        return found_files
            elif "-" in minicore_id:
                q = minicore_id.replace("-", "_")
                found = [
                    file
                    for file in files
                    if f"{q}" in file["file_name"]
                    or f"{q}-" in file["file_name"]
                    or f"{q}." in file["file_name"]
                ]
                found_files.extend(found)
                if found_files:
                    return found_files
            return False

####################################################
# CHANGE THE BELOW 3 VARIABLES TO CHANGE QUERY KEY #
####################################################

    pref_id = sample.get("Preferred Sequence ID")
    minicore_id = sample.get("minicore_seq_id")
    old_minicore_id = sample.get("old_minicore_seq_id")
    name = sample.get("*sample_name")
    found_files = False
    # print(minicore_id)
    # print(pref_id)
    #  Search using minicore "Actual sequence id" first.
    #   For conflict resolution, return False to use *sample_name, return true to use preferred seq ID
    found_files = find_files(minicore_id)
    if found_files:
        found_files = [f for f in found_files if f["file_name"].endswith(".gz")]
        return (sample, False, found_files)
    
    # #  Search using pref id first.
    # found_files = find_files(pref_id)
    # if found_files:
    #     found_files = [f for f in found_files if f["file_name"].endswith(".gz")]
    #     return (sample, True, found_files)

    # #  We get here if above didnt return anything so we search w/ samplename, dont want to do this
    #found_files = find_files(sample.get("*sample_name"))
    #if found_files:
        #found_files = [f for f in found_files if f["file_name"].endswith(".gz")]
        #return (sample, False, found_files)
    

    return False


def solve_conflict(file: str, samples: list[dict[dict]]) -> str:
    """Try to figure out which sample best fits filename using fuzzy matching"""

    ratios = {}

    for s in samples:
        name = s["sample"]["*sample_name"]
        pref_id_bool = s["pref_id_bool"]

        if pref_id_bool:
            ratios[name] = fuzz.ratio(str(s["sample"]["Preferred Sequence ID"]), file)

        else:
            ratios[name] = fuzz.ratio(str(s["sample"]["*sample_name"]), file)
    logging.debug(f" Conflict for {file}, samples: {samples}, ratios{ratios}")
    return max(ratios, key=lambda k: ratios[k])


def link_files_to_metadata(db_client: pymongo.MongoClient):
    """Links fastq files to sample names in samples db."""

    db = db_client["ccgp_dev"]
    metadata = db["sample_metadata"]
    reads_db = db["reads"]
    metadata.update_many(
    {"files": {"$in": ["", "NaN"]}},
    {"$pull": {"files": {"$in": ["", "NaN"]}}}
    )
    #sample_names = list(metadata.find({}))  # Get all samples regardless if it has files b/c they might want new files
    #THIS IS THE PLACE TO DO SPECIFIC SAMPLES

    sample_names = list(metadata.find({"ccgp-project-id": "93-Brachycybe"})) 
    #sample_names = list(metadata.find({"*sample_name": "811"})) 
    
    reads = list(reads_db.find({}))
    print(f"Found {len(reads)} reads.")
    print(f"Found {len(sample_names)} samples.")
    metadata_ops = []
    reads_ops = []
    matches = 0
    matched_files = 0
    match_dict = defaultdict(list)

    for sample in sample_names:
        # print(sample)
        name = sample["*sample_name"]
        print(f"Searching for {name}")
        
        
        mc_seq= sample.get("minicore_sequenced")
        if mc_seq == "YES":
            mc_seq = 1
        elif mc_seq == "NO":
            mc_seq = None
        
        if mc_seq is None or math.isnan(mc_seq):
            print(f"sample: {name} not sequenced, skipping")
            continue
        found_files = []
        search_result = search(sample, reads)

        if search_result:
            matched_sample, used_pref_id, found_files = search_result

            
            print("Found files:", found_files)

        if found_files:
            #print(f"Found files for {name}: {found_files}")
            matches += 8
            matched_files += len(found_files)
            dates = [file["mdate"] for file in found_files][0]
            filesize_sum = sum([file["filesize"] for file in found_files])

            #files = [file["file_name"] for file in found_files] ## THIS WAS THE ORIGINAL CODE.

            existing_files = sample.get("files", [])

            # If for some reason update_reads doesnt work next time, add the existing_files line back.
            #existing_files = [file for file in existing_files if file and file.lower() != "nan"]
            new_files_to_add = [file for file in found_files if file["file_name"] not in existing_files] 

            if len(found_files) >= 20:
                logging.warning(
                    f"Found abnormal number of files ({len(found_files)}) for sample '{name}'"
                )


### NEW CODE TO TEST -- Doesnt append duplicate files to MongoDB ###
            if new_files_to_add:
                metadata_ops.append(
                    pymongo.operations.UpdateOne(
                        filter={"*sample_name": name},
                        update={
                            "$addToSet": {
                                "files": {"$each": [file["file_name"] for file in new_files_to_add]}

                            },
                            "$set": {
                                "received": dates,
                                "filesize_sum": filesize_sum,

                            },
                        },

                    )
                )
                existing_files.extend([file["file_name"] for file in new_files_to_add])
            
            for file in new_files_to_add:
                logging.debug(f"Matched {name} with {file}")
                reads_ops.append(
                    pymongo.operations.UpdateOne(
                        filter={"file_name": file},
                        update={
                            "$set": {"orphan": False},
                        },
                    )
                )
        else:
            print(f"No files found for {name}")
########################################################

                ### ORIGINAL CODE ###
        #     for file in files:
        #         logging.debug(f"Matched {name} with {file}")
        #         match_dict[file].append(
        #             {
        #                 "sample": matched_sample,
        #                 "pref_id_bool": used_pref_id,
        #             }
        #         )
        #         reads_ops.append(
        #             pymongo.operations.UpdateOne(
        #                 filter={"file_name": file},
        #                 update={
        #                     "$set": {"orphan": False},
        #                 },
        #             )
        #         )
        #     # print(files)
        #     metadata_ops.append(
        #         pymongo.operations.UpdateOne(
        #             filter={"*sample_name": name},
        #             update={
        #                 "$set": {
        #                     "files": files,
        #                     "recieved": dates,
        #                     "filesize_sum": filesize_sum,
        #                 },
        #             },
        #         )
        #     )
        # else:
        #     print("got here")
        #     files=[]
        #     dates=np.nan
        #     filesize_sum=0
        #     metadata_ops.append(
        #         pymongo.operations.UpdateOne(
        #             filter={"*sample_name": name},
        #             update={
        #                 "$set": {
        #                     "files": files,
        #                     "recieved": dates,
        #                     "filesize_sum": filesize_sum,
        #                 },
        #             },
        #         )
        #     )
            
        #     logging.warning(f"No files found for {name}")
            # continue

    for k, v in match_dict.items():

        if len(v) > 1:
            matched_samples = [s["sample"]["*sample_name"] for s in v]

            logging.warning(
                f" File: '{k}' has multiple samples associated with it: {matched_samples}"
            )
            best_match = solve_conflict(k, v)
            matches_to_drop = [i for i in matched_samples if i != best_match]
            logging.warning(
                f" Pulling file: '{k}' from samples: {matches_to_drop} because '{best_match}' matched the file best."
            )
            for match in matches_to_drop:
                metadata_ops.append(
                    pymongo.operations.UpdateOne(
                        filter={"*sample_name": match},
                        update={"$pull": {"files": k}},
                    )
                )

    # if not matched_files:
    #     logging.info(" Found no matches, exiting.")
    #     return
    logging.info(f" Matched {matched_files} orphan files with {matches} samples")
    # print("got here too", metadata_ops)
    if metadata_ops:
        try:
            metadata.bulk_write(metadata_ops)
            reads_db.bulk_write(reads_ops)
            print('test')
        except BulkWriteError as bwe:
            logging.error(bwe.details)


def main():
    files = list_s3_bucket_objs()
    db_client = get_mongo_client()
    update_db_all(db_client, files)
    link_files_to_metadata(db_client)


if __name__ == "__main__":
    main()
