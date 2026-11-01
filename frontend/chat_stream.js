/* Same-origin SSE transport. A broken stream is recovered through the saved turn. */
window.ButlerChatStream=async function(body,signal,onProgress){
  const response=await fetch('/api/chat/stream',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','Accept':'text/event-stream'},signal,body:JSON.stringify(body)});
  if(!response.ok){const error=new Error('Streaming-Anfrage fehlgeschlagen');error.status=response.status;throw error;}
  if(!response.body)throw new Error('Streaming ist in diesem Browser nicht verfügbar.');
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
  try{
    while(true){
      const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});buffer=buffer.replace(/\r\n/g,'\n');
      if(buffer.length>1024*1024)throw new Error('Streaming-Nachricht zu groß');
      let boundary;
      while((boundary=buffer.indexOf('\n\n'))>=0){
        if(signal.aborted)throw new DOMException('Aborted','AbortError');
        const block=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);
        const type=block.split('\n').find(line=>line.startsWith('event:'))?.slice(6).trim();
        const data=block.split('\n').filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');
        if(!data)continue;const parsed=JSON.parse(data);
        if(type==='result')return parsed;
        if(type==='progress')onProgress?.({progress:parsed,draft_text:parsed.text});
      }
      if(done)throw new Error('Stream unterbrochen; gespeicherte Antwort wird weiter geprüft.');
    }
  }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
};
