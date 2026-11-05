/* Transfer small native-rate mono blocks; the audio thread never runs recognition. */
class ButlerCapture extends AudioWorkletProcessor{
  constructor(){super();this.buffer=new Float32Array(2048);this.used=0;this.stopped=false;
    this.port.onmessage=event=>{if(event.data==='stop'){this.stopped=true;this.flush();this.port.postMessage({stopped:true});}};
  }
  flush(){if(this.used){const block=this.buffer.slice(0,this.used);this.port.postMessage(block,[block.buffer]);this.used=0;}}
  process(inputs){
    if(this.stopped)return false;
    const input=inputs[0]?.[0];if(!input)return true;
    for(const value of input){this.buffer[this.used++]=value;if(this.used===this.buffer.length)this.flush();}
    return true;
  }
}
registerProcessor('butler-capture',ButlerCapture);
