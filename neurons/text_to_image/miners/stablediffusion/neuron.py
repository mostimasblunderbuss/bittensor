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

import torch
import argparse
import bittensor

from diffusers import StableDiffusionPipeline
from typing import List, Dict, Union, Tuple, Optional

def config():       
    parser = argparse.ArgumentParser( description = 'Stable Diffusion Text to Image Miner' )
    parser.add_argument( '--model_name', type=str, help='Name of the diffusion model to use.', default = "runwayml/stable-diffusion-v1-5" )
    parser.add_argument( '--device', type=str, help='Device to load model', default="cuda:0" )
    bittensor.base_miner_neuron.add_args( parser )
    return bittensor.config( parser )

def main( config ):

    # --- Build the base miner
    base_miner = bittensor.base_miner_neuron( netuid = 14, config = config )

    # --- Build diffusion pipeline ---
    pipe = StableDiffusionPipeline.from_pretrained( config.model_name, torch_dtype=torch.float16).to( config.device )

    # --- Build Synapse ---
    class SDTextToImageSynapse( bittensor.TextToImageSynapse ):

        def priority( self, forward_call: "bittensor.SynapseCall" ) -> float: 
            return base_miner.priority( forward_call )

        def blacklist( self, forward_call: "bittensor.SynapseCall" ) -> Union[ Tuple[bool, str], bool ]:
            return base_miner.blacklist( forward_call )
        
        def forward( self, text: str ) -> bytes:
            image = pipe( text ).images[0] 
            return image
        
    # --- Attach the synapse to the miner ----
    base_miner.axon.attach( SDTextToImageSynapse() )

    # --- Run Miner ----
    base_miner.run()

if __name__ == "__main__":
    bittensor.utils.version_checking()
    main( config() )





