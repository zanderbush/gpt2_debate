import numpy as np
import os
import io

# import for server
from flask import Flask, render_template, request, Response, send_file, jsonify
from queue import Queue, Empty
import threading
import time

# import for model
from transformers import AutoTokenizer, AutoModelWithLMHead, top_k_top_p_filtering
from torch.nn import functional as F
import torch
import time

# flask server
app = Flask(__name__)

# limit input file size under 2MB

# model loading
tokenizer = AutoTokenizer.from_pretrained("gpt2-large")
model = AutoModelWithLMHead.from_pretrained("gpt2-large", return_dict=True)

# change cpu to gpu so that model can use gpu (because default type is cpu)
device = torch.device('cpu')
model.to(device)

# request queue setting
requests_queue = Queue()
BATCH_SIZE = 1
CHECK_INTERVAL = 0.1

# static variable

# request handling
def handle_requests_by_batch():
    try:
        while True:
            requests_batch = []
            while not (len(requests_batch) >= BATCH_SIZE):
                try:
                    requests_batch.append(requests_queue.get(timeout=CHECK_INTERVAL))
                except Empty:
                    continue
                
            batch_outputs = []

            for request in requests_batch:
                if len(request["input"]) == 2:
                    batch_outputs.append(run_short(request["input"][0], request["input"][1]))
                elif len(request["input"]) == 3:
                    batch_outputs.append(run_long(request["input"][0], request["input"][1], request["input"][2]))

            for request, output in zip(requests_batch, batch_outputs):
                request["output"] = output

    except Exception as e:
        while not requests_queue.empty():
            requests_queue.get()
        print(e)


# request processing
threading.Thread(target=handle_requests_by_batch).start()

# run short model
def run_short(prompt, num):
    try:
        prompt = prompt.strip()
        input_ids = tokenizer.encode(prompt, return_tensors='pt')
        
        # input_ids also need to apply gpu device!
        input_ids = input_ids.to(device)

        # get logits of last hidden state
        next_token_logits = model(input_ids).logits[:, -1, :]
        # filter
        filtered_next_token_logits = top_k_top_p_filtering(next_token_logits, top_k=50, top_p=1.0)
        # sample
        probs = F.softmax(filtered_next_token_logits, dim=-1)
        next_token = torch.multinomial(probs, num_samples=num)

        result = {}
        for idx, token in enumerate(next_token.tolist()[0]):
            result[idx] = tokenizer.decode(token)

        return result

    except Exception as e:
        print(e)
        return 500

# run long model
def run_long(prompt, num, length):
    try:
        
        bad_word_ids = [
            [10134],
            [318], 
            [1716], 
            [373], 
            [655],
            [198],
            [468],
            [1394],
            [1464],
            [790],
            [4477],
            [867],
            [3236],
            [4858],
            [1588],
            [1263],
            [1029],
            [3607],
            [1838],
            [1049],
            [9812],
            [12465],
            [2048],
            [617],
            [423],
            [7448],
            [389],
            [550],
            [1595],
            [470],
        ]
        
        prompt = prompt.strip()
        input_ids = tokenizer.encode(prompt, return_tensors='pt')
        
        # input_ids also need to apply gpu device!
        input_ids = input_ids.to(device)

        min_length = len(input_ids.tolist()[0])
        length += min_length

        sample_outputs = model.generate(input_ids, pad_token_id=50256, 
                                        do_sample=True, 
                                        max_length=length, 
                                        min_length=length,
                                        top_k=40,
                                        num_return_sequences=num,
                                        bad_words_ids = bad_word_ids)

        generated_texts = {}
        for i, sample_output in enumerate(sample_outputs):
            output = tokenizer.decode(sample_output.tolist()[min_length:], skip_special_tokens=True)
            generated_texts[i] = output
        
        return generated_texts

    except Exception as e:
        print(e)
        return 500

# routing
@app.route("/gpt2-debate/<types>", methods=['POST'])
def generation(types):
    try:
        if types != 'short' and types != 'long':
            return jsonify({'message' : 'Error! Can not route short or long'}), 400

        # only get one request at a time
        if requests_queue.qsize() > BATCH_SIZE:
            return jsonify({'message' : 'TooManyReqeusts'}), 429
    
        # check image format
        try:
            args = []

            prompt = str(request.form['text'])
            num = int(str(request.form['num_samples']))
            
            args.append(prompt)
            args.append(num)

            if types == 'long':
                length = int(str(request.form['length']))
                args.append(length)
            
        except Exception:
            return jsonify({'message' : 'Error! Can not read args from request'}), 500

        # put data to request_queue
        req = {'input' : args}
        requests_queue.put(req)
        
        # wait output
        while 'output' not in req:
            time.sleep(CHECK_INTERVAL)
       
        # send output
        generated_text = req['output']
        
        if generated_text == 500:
            return jsonify({'message': 'Error! An unknown error occurred on the server'}), 500
        
        result = jsonify(generated_text)
        
        return result
    
    except Exception as e:
        print(e)
        return jsonify({'message': 'Error! Unable to process request'}), 400

@app.route('/healthz')
def health():
    return "ok", 200

@app.route('/')
def main():
    return "ok", 200

if __name__ == "__main__":
    from waitress import serve
    serve(app, host='0.0.0.0', port=80)
