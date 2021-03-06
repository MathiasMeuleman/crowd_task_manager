import pika
import pathlib
import os
import json
import sys

sys.path.append("..")
import common.settings as settings
import common.file_system_manager as fsm

from slicer import Score, Slice
from pymongo import MongoClient


address = settings.rabbitmq_address
connection = pika.BlockingConnection(pika.ConnectionParameters(address[0], address[1]))
channel = connection.channel()
channel.queue_declare(queue=settings.sheet_queue_name)

def callback(ch, method, properties, body):
    data = json.loads(body)
    name = data['name']

    print(f"Processing score {name}")

    path = fsm.get_sheet_base_directory(name)
    score = Score(str(path))

    out_path = fsm.get_sheet_slices_directory(name)
    measure_path = out_path / "measures"
    line_path = out_path / "lines" 
    double_measure_path = out_path / "double_measures"

    slice_paths_lists = {
        measure_path            : score.get_measure_slices(),
        double_measure_path     : score.get_measure_slices(2),
        line_path               : score.get_line_slices()
    }

    client = MongoClient(settings.mongo_address[0], int(settings.mongo_address[1]))
    db = client.trompa_test

    for slice_path, slice_list in slice_paths_lists.items():
        pathlib.Path(slice_path).mkdir(parents=True, exist_ok=True)
        for score_slice in slice_list:
            if score_slice.same_page:
                score_slice.get_image().save(str(slice_path / score_slice.get_name()))
                slice_res = db[settings.slice_collection_name].insert_one(score_slice.to_db_dict())
                print(f"added entry {slice_res.inserted_id} to slices collection")

    channel.queue_declare(queue = settings.score_queue_name)

    score_res = db[settings.score_collection_name].insert_one(score.to_db_dict())
    print(f"added entry {score_res.inserted_id} to scores collection")

    status_update_msg = {
        '_id': data['_id'],
        'module': 'slicer',
        'status': 'complete',
        'name': name}

    channel.basic_publish(exchange='',
        routing_key='status_queue',
        body=json.dumps(status_update_msg))
    print(f"Published processed score {score.name} to message queue!")

channel.basic_consume(queue='slicer_queue', on_message_callback=callback, auto_ack=True)

print('Score slicer is listening...')
channel.start_consuming()