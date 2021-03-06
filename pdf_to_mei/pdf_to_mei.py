import sys
import pika
import json

sys.path.append("..")
import common.settings as settings
import common.file_system_manager as fsm

import measure_detector.folder_to_mei as to_mei

from pymongo import MongoClient
from bson.objectid import ObjectId
from pdf2image import convert_from_path
from pathlib import Path


connection = pika.BlockingConnection(pika.ConnectionParameters(
    settings.rabbitmq_address[0],
    settings.rabbitmq_address[1]
    ))
channel = connection.channel()
channel.queue_declare(queue=settings.new_item_queue_name)


def add_to_queue(queue, routing_key, msg):
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(
            host=settings.rabbitmq_address[0],
            port=settings.rabbitmq_address[1]))
    channel = connection.channel()
    channel.queue_declare(queue=queue)
    channel.basic_publish(exchange='', routing_key=routing_key, body=msg)
    connection.close()


def callback(ch, method, properties, body):
    # Decode body and obtain pdf id
    data = json.loads(body)
    pdf_id = data['_id']

    # Initiate mongo client and sheet collection
    client = MongoClient(
        settings.mongo_address[0],
        int(settings.mongo_address[1]))
    db = client.trompa_test
    sheet_collection = db[settings.sheet_collection_name]

    # Get PDF sheet entry
    pdf_sheet = sheet_collection.find_one(ObjectId(pdf_id))
    print(pdf_sheet)
    pdf_sheet_path = Path(pdf_sheet["sheet_path"])
    pdf_sheet_name = pdf_sheet_path.stem
    if not pdf_sheet:
        raise Exception(f"PDF Sheet under id {pdf_id} does not exist!")

    # PDF -> JPEG
    print("Converting PDF to JPEG page images...")
    pages = convert_from_path(pdf_sheet_path.absolute(), 300)
    img_pages_path = fsm.get_sheet_pages_directory(pdf_sheet_name)
    for index, page in enumerate(pages):
        page_path = img_pages_path / f'page_{index}.jpg'
        page.save(page_path, 'JPEG')
        sheet_collection.update_one({'sheet_path': str(pdf_sheet_path)},
                                    {'$push': {'pages_path': str(page_path)}},
                                    upsert=True)
        print(f"{index} pages out of {len(pages)}")
    print("DONE")

    # JPEG -> MEI
    print("Converting JPEG pages to MEI skeleton...")
    to_mei.run(pdf_sheet_name)

    # Update sheet on mongo
    # TODO: This doesn't seem necessary given that the mei will always be called "aligned.mei", the fsm can handle the paths
    mei_path = fsm.get_sheet_whole_directory(pdf_sheet_name) / "aligned.mei"
    sheet_collection.update_one({'_id': ObjectId(pdf_id)},
                                {'$push': {'mei_path': str(mei_path)}},
                                upsert=True)

    # Output name to sheet queue
    status_update_msg = {
        '_id': pdf_id,
        'module': 'measure_detector',
        'status': 'complete',
        'name': pdf_sheet_name}
    add_to_queue(
        'status_queue',
        'status_queue',
        json.dumps(status_update_msg))
    print(f"Published PDF->MEI converted sheet {pdf_sheet_name} to message queue!")


def main():
    try:
        print('PDF to MEI converter is listening...')
        while True:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=settings.rabbitmq_address[0],
                    port=settings.rabbitmq_address[1]))
            channel = connection.channel()
            method_frame, header_frame, body = channel.basic_get(settings.new_item_queue_name)
            if method_frame:
                channel.basic_ack(method_frame.delivery_tag)
                callback(channel, method_frame, '', body)
    except KeyboardInterrupt:
        print('interrupted!')


if __name__ == "__main__":
    main()
