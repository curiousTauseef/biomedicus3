# Copyright 2019 Regents of the University of Minnesota.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
import signal
from pathlib import Path
from subprocess import Popen, TimeoutExpired, PIPE

import grpc
import pytest
from nlpnewt import EventsClient, Pipeline, RemoteProcessor, LocalProcessor
from nlpnewt.io.serialization import get_serializer
from nlpnewt.metrics import Accuracy, Metrics
from nlpnewt.utils import find_free_port


@pytest.fixture(name='acronyms_service')
def fixture_acronyms_service(events_service):
    port = str(find_free_port())
    address = '127.0.0.1:' + port
    biomedicus_jar = os.environ['BIOMEDICUS_JAR']
    p = Popen(['java', '-cp', biomedicus_jar, 'edu.umn.biomedicus.acronym.AcronymDetectorProcessor',
               '-p', port, '--events', events_service],
              start_new_session=True, stdin=PIPE, stdout=PIPE, stderr=PIPE)
    try:
        if p.returncode is not None:
            raise ValueError('subprocess terminated')
        with grpc.insecure_channel(address) as channel:
            future = grpc.channel_ready_future(channel)
            future.result(timeout=60)
        yield address
    finally:
        p.send_signal(signal.SIGINT)
        try:
            stdout, _ = p.communicate(timeout=1)
            print("python processor exited with code: ", p.returncode)
            print(stdout.decode('utf-8'))
        except TimeoutExpired:
            print("timed out waiting for python processor to terminate.")


@pytest.mark.performance
def test_acronyms_performance(events_service, acronyms_service):
    input_dir = Path(os.environ['BIOMEDICUS_TEST_DATA']) / 'acronyms'
    json_serializer = get_serializer('json')

    top_score_accuracy = Accuracy(name='top_score_accuracy', fields=['expansion'])
    any_accuracy = Accuracy(name='any_accuracy', mode='any', fields=['expansion'])
    detection_accuracy = Accuracy(name='detection_accuracy', mode='location', fields=['expansion'])
    with EventsClient(address=events_service) as client, Pipeline(
        RemoteProcessor(processor_id='biomedicus-acronyms', address=acronyms_service),
        LocalProcessor(Metrics(top_score_accuracy, detection_accuracy, tested='acronyms',
                               target='gold_acronyms'),
                       component_id='top_score_metrics', client=client),
        LocalProcessor(Metrics(any_accuracy, tested='all_acronym_senses', target='gold_acronyms'),
                       component_id='all_senses_metrics', client=client)
    ) as pipeline:
        for test_file in input_dir.glob('**/*.json'):
            with json_serializer.file_to_event(test_file, client=client) as event:
                document = event['plaintext']
                pipeline.run(document)

        print('Top Sense Accuracy:', top_score_accuracy.value)
        print('Any Sense Accuracy:', any_accuracy.value)
        print('Detection Accuracy:', detection_accuracy.value)
        pipeline.print_times()
        assert top_score_accuracy.value > 0.4
        assert any_accuracy.value > 0.4
        assert detection_accuracy.value > 0.65