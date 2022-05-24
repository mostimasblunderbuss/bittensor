""" Unit test for tokenizer utilities.
"""
# The MIT License (MIT)
# Copyright © 2021 Yuma Rao

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import bittensor

from transformers import AutoTokenizer, AutoModelForCausalLM
from torch import nn
from torch.nn.utils.rnn import pad_sequence
from bittensor.utils.tokenizer_utils import *

EPSILON = 1e-64

sample_text = {'English-1': ['''The Three Laws of Robotics (often shortened to The Three Laws or known as Asimov's Laws) are a set of rules devised by science fiction author Isaac Asimov. The rules were introduced in his 1942 short story "Runaround" (included in the 1950 collection I, Robot), although they had been foreshadowed in some earlier stories. The Three Laws, quoted from the "Handbook of Robotics, 56th Edition, 2058 A.D.", are:''',

                             '''(Zeroth Law: A robot may not harm humanity, or, by inaction, allow humanity to come to harm.)
First Law: A robot may not injure a human being or, through inaction, allow a human being to come to harm.
Second Law: A robot must obey the orders given it by human beings except where such orders would conflict with the First Law.
Third Law: A robot must protect its own existence as long as such protection does not conflict with the First or Second Law.'''
                             ],


               'German-1': ['''Die Drei Gesetze der Robotik (oft abgekürzt als Die Drei Gesetze oder bekannt als Asimovs Gesetze) sind eine reihe von regeln, die vom Science-Fiction-Autor Isaac Asimov entwickelt wurden. Die regeln wurden in seiner kurzgeschichte "Runaround" von 1942 (in der sammlung I, Robot von 1950 enthalten) eingeführt, obwohl sie in einigen früheren geschichten angedeutet worden waren. Die Drei Gesetze, zitiert aus dem "Handbook of Robotics, 56th Edition, 2058 A.D.", sind:''',

                            '''(Nulltes Gesetz: Ein roboter darf der menschheit keinen schaden zufügen oder durch untätigkeit zulassen, dass der menschheit schaden zugefügt wird.)
Erstes Gesetz: Ein roboter darf einen menschen nicht verletzen oder durch untätigkeit zulassen, dass einem menschen schaden zugefügt wird.
Zweites Gesetz: Ein roboter muss den ihm von menschen erteilten befehlen gehorchen, es sei denn, solche befehle würden im widerspruch zum Ersten Gesetz stehen.
Drittes Gesetz: Ein roboter muss seine eigene existenz schützen, solange dieser schutz nicht im widerspruch zum Ersten oder Zweiten Gesetz steht.'''
                            ]}


def test_tokenizer_equivalence():
    r"""
    Checks if two tokenizers are equivalent w.r.t. their vocabularies.
    Equivalent tokenizers should always produce the same tokenization for the same text.
        Returns:
            Asserts expected result for list of tokenizer pairs.
    """
    test_pairs = [('gpt2', 'gpt2', True),
                  ('gpt2', 'EleutherAI/gpt-neo-125M', True),
                  ('gpt2', 'EleutherAI/gpt-neo-2.7B', True),
                  ('gpt2', 'EleutherAI/gpt-j-6B', True),
                  ('gpt2', 'KoboldAI/fairseq-dense-2.7B', False),
                  ('gpt2', 'bert-base-uncased', False),
                  ('gpt2', 'xlnet-base-cased', False),
                  ('gpt2', 'facebook/xglm-564M', False),
                  ('gpt2', 'benjamin/gerpt2-large', False)]

    for target, to_check, expected_result in test_pairs:
        tokenizer_to_check = AutoTokenizer.from_pretrained(to_check)
        target_tokenizer = AutoTokenizer.from_pretrained(target)
        assert check_tokenizer_equivalence(tokenizer_to_check, target_tokenizer) == expected_result


def get_loss_fct(logits: torch.FloatTensor, labels: torch.LongTensor) -> torch.FloatTensor:
    """
    Calculate loss_fct, CausalLM loss, next-token prediction loss.
        Args:
            logits (:obj:`torch.FloatTensor`, `required`):
                [batch_size, sequence_len, bittensor.__network_dim__]
            labels (:obj:`torch.LongTensor`, `required`):
                [batch_size, sequence_len]

        Returns:
            loss (:obj:`torch.FloatTensor`):
                scalar
    """
    shift_logits = logits[..., :-1, :].contiguous()
    shift_labels = labels[..., 1:].contiguous()
    loss = nn.CrossEntropyLoss()(shift_logits.view(-1, shift_logits.size(-1)), shift_labels.view(-1))

    return loss


def encode_forward_response_tensor(forward_response_tensor: torch.Tensor, topk: int = 512) -> torch.FloatTensor:
    """ Returns topk tokens/probabilities given unnormalized logits as input. """
    logits = forward_response_tensor  # unnormalized logit scores: [batch_size, sequence_len, vocab_size]
    probs = torch.softmax(logits, dim=-1)  # normalized probabilities: [batch_size, sequence_len, vocab_size]

    values, indices = probs.sort(dim=-1, descending=True)  # descend sort probs
    topk_values = values[..., :topk]  # topk probs: [batch_size, sequence_len, topk]
    topk_indices = indices[..., :topk]  # topk probs indices: [batch_size, sequence_len, topk]
    encoded_probs = torch.cat((topk_values, topk_indices), dim=-1)  # [batch_size, sequence_len, topk + topk]

    return encoded_probs  # [batch_size, sequence_len, topk + topk]


def decode_forward_response_tensor(forward_response_tensor: torch.Tensor,
                                   vocab_size=bittensor.__vocab_size__, topk: int = 512) -> torch.FloatTensor:
    """ Returns full logits by decoding topk-encoding input. """
    batch_size, sequence_len, _ = forward_response_tensor.shape
    encoded_probs = forward_response_tensor  # encoded probabilities: [batch_size, sequence_len, topk + topk]
    topk_values = encoded_probs[..., :topk]  # topk probs: [batch_size, sequence_len, topk]
    topk_indices = encoded_probs[..., topk:].long()  # topk probs indices: [batch_size, sequence_len, topk]

    topk_pmass = topk_values.sum(dim=-1)  # topk probability mass: [batch_size, sequence_len]
    remainder_pmass = torch.clamp(1 - topk_pmass, 1e-64, 1)  # remainder probability mass: [batch_size, sequence_len]
    remainder_floor = remainder_pmass / (vocab_size - topk)  # divide remainder: [batch_size, sequence_len]

    logits = torch.ones((batch_size, sequence_len, vocab_size)).to(topk_values.device)
    logits *= torch.log(remainder_floor)[:, :, None]  # set probability floor: [batch_size, sequence_len, vocab_size]
    logits.scatter_(-1, topk_indices, torch.log(topk_values + 1e-64))  # insert topk probs: [batch_size, sequence_len, vocab_size]

    return logits  # [batch_size, sequence_len, vocab_size]


def tokenizer_translation(text_batch: List[str], model_name: str, max_length: int,
                          enc_pre_logits: torch.FloatTensor = None,
                          device: str = 'cuda', topk: int = 512) -> Tuple[torch.FloatTensor,
                                                                          torch.FloatTensor,
                                                                          torch.FloatTensor,
                                                                          torch.FloatTensor]:
    r"""
    Emulates validator -> server -> validator interaction where the server-side logit translation
    to standard token probabilities allow the validator to calculate standard loss without
    having to know any server tokenizer/model/decoder particulars.
    Topk encoding is only used to save the server model response to avoid CUDA-device requirement
    when routinely running the unit test.
        Args:
            text_batch (:obj:`List[str]`, `required`):
                Input text_batch to test tokenizer translation with.
            model_name (:obj:`str`, `required`):
                Name of transformer model to use as template server.
            max_length (:obj:`int`, `required`):
                Specific tokenization max length, small enough to prevent padding,
                since GPT2 tokenization doesn't have padding.
            enc_pre_logits (:obj:`torch.FloatTensor`, `optional`):
                [batch_size, sequence_len, vocab_size] Encoded pre_logits from saved source, to
                bypass server model forward call.
            device (:obj:`str`, `optional`):
                CUDA device for server model forward call.
            topk (:obj:`int`, `optional`):
                Amount of top logits to encode the server model pre_logits with (for saving purposes).

        Returns:
            original_loss (:obj:`torch.FloatTensor`, `required`):
                Original server model loss, before any encoding/compression.
            encoded_loss (:obj:`torch.FloatTensor`, `required`):
                Loss after server model logits have been topk encoded/compressed.
            translated_loss (:obj:`torch.FloatTensor`, `required`):
                Standard loss after logit translation to standard probabilities.
            enc_pre_logits (:obj:`torch.FloatTensor`, `required`):
                [batch_size, sequence_len, vocab_size] Encoded pre_logits.
    """
    # =============================================
    # ==== Validator-side: CausalLM task setup ====
    # =============================================

    std_tokenizer = AutoTokenizer.from_pretrained('gpt2')

    input_batch = std_tokenizer(text_batch, return_offsets_mapping=True, add_special_tokens=False,
                                max_length=max_length, truncation=True, return_tensors='pt')

    token_batch = input_batch['input_ids']

    # ============================
    # ==== Server-side: Setup ====
    # ============================

    tokenizer = AutoTokenizer.from_pretrained(model_name)

    to_translation_map = get_translation_map(tokenizer, std_tokenizer)
    from_translation_map = get_translation_map(std_tokenizer, tokenizer)
    split_map_cache = {}

    # ================================================
    # ==== Server-side: CausalLM task translation ====
    # ================================================

    text_batch = std_tokenizer.batch_decode(token_batch)  # decode tokens to original text
    to_text_batch, from_offsets_batch, to_offsets_batch, pad_offsets_batch = translate_special_token_text(text_batch,
                                                                                                          std_tokenizer,
                                                                                                          tokenizer)

    std_tokens = std_tokenizer(text_batch, return_offsets_mapping=True)  # encode to get offsets
    tokens = tokenizer(to_text_batch, return_offsets_mapping=True, add_special_tokens=False)

    std_tokens['offset_mapping'] = pad_offsets(std_tokens['offset_mapping'], from_offsets_batch, pad_offsets_batch)
    tokens['offset_mapping'] = pad_offsets(tokens['offset_mapping'], to_offsets_batch, pad_offsets_batch)

    tokens['offset_mapping_std'] = std_tokens['offset_mapping']

    for key in ['input_ids', 'attention_mask']:
        tokens[key] = pad_sequence([torch.LongTensor(tensor) for tensor in tokens[key]], batch_first=True)
        tokens[key] = torch.LongTensor(tokens[key])

    # ==============================================
    # ==== Server-side: CausalLM task execution ====
    # ==============================================

    original_loss = None

    if enc_pre_logits is None:
        server_model = AutoModelForCausalLM.from_pretrained(model_name).to(device)

        with torch.no_grad():
            token_batch = input_batch['input_ids'].to(device)
            pre_model_output = server_model(input_ids=tokens['input_ids'].to(device),
                                            attention_mask=tokens['attention_mask'].to(device),
                                            output_hidden_states=True)
            pre_logits = pre_model_output.logits

        original_loss = get_loss_fct(pre_logits.cpu(), tokens['input_ids'])
        enc_pre_logits = encode_forward_response_tensor(pre_logits, topk=topk).cpu()

    dec_pre_logits = decode_forward_response_tensor(enc_pre_logits, tokenizer.vocab_size, topk=topk)
    encoded_loss = get_loss_fct(dec_pre_logits, tokens['input_ids'])

    # ============================================
    # ==== Server-side: Tokenizer translation ====
    # ============================================

    with torch.no_grad():
        probs_std = translate_logits_to_probs_std(dec_pre_logits.cpu(),
                                                  tokens['offset_mapping'], tokens['offset_mapping_std'],
                                                  tokenizer, std_tokenizer,
                                                  split_map_cache, to_translation_map, from_translation_map,
                                                  tokens['input_ids'].cpu(), token_batch.cpu(),
                                                  skip_equivalent=False)

    logits_std = torch.log(probs_std + EPSILON)
    translated_loss = get_loss_fct(logits_std, token_batch.cpu())

    return original_loss, encoded_loss, translated_loss, enc_pre_logits


def test_tokenizer_translation():
    r"""
    Unit test for tokenizer translation.

        Returns:
            Asserts that tokenizer translation produces previous encoded and translated losses.
    """
    test_pairs = [('English-1', 'EleutherAI/gpt-j-6B', 95),
                  ('English-1', 'benjamin/gerpt2-large', 95),
                  ('German-1', 'benjamin/gerpt2-large', 172)]

    encodings_cache_file = 'test_tokenizer_utils.pt'

    try:
        encodings = torch.load(encodings_cache_file)

    except FileNotFoundError as e:
        print('FileNotFoundError: Server model results not yet saved to', encodings_cache_file)
        print('Will first run server models (requires CUDA)...')

        # === Run server models to obtain encoded logits ===
        encodings = {}
        for text_name, model_name, max_length in test_pairs:
            result = tokenizer_translation(sample_text[text_name], model_name, max_length, topk=128)
            original_loss, encoded_loss, translated_loss, enc_pre_logits = result
            encodings[(text_name, model_name)] = (encoded_loss, translated_loss, enc_pre_logits)

            print(text_name, model_name, original_loss, encoded_loss, translated_loss)

            # English-1 EleutherAI/gpt-j-6B tensor(1.2531) tensor(1.3274) tensor(1.3274)
            # English-1 benjamin/gerpt2-large tensor(3.7499) tensor(4.2219) tensor(4.5502)
            # German-1 benjamin/gerpt2-large tensor(3.5197) tensor(4.0664) tensor(4.1428)

        torch.save(encodings, encodings_cache_file)
        encodings = torch.load(encodings_cache_file)

    # === Run token translations on saved encoded logits ===
    for text_name, model_name, max_length in test_pairs:
        _encoded_loss, _translated_loss, _enc_pre_logits = encodings[(text_name, model_name)]
        result = tokenizer_translation(sample_text[text_name], model_name, max_length, _enc_pre_logits, topk=128)
        original_loss, encoded_loss, translated_loss, enc_pre_logits = result

        assert torch.isclose(encoded_loss, _encoded_loss, rtol=1e-2)
        assert torch.isclose(translated_loss, _translated_loss, rtol=1e-2)


if __name__ == '__main__':
    test_tokenizer_equivalence()
    test_tokenizer_translation()