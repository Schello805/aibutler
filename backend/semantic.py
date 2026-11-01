"""CPU-only local embeddings and embedded Qdrant. No inference network calls."""
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid
from .retrieval import message_text

MODEL='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
DIMENSION=384
FORMAT='minilm-multilingual-windows350-v1'


def model_info(root):
    try:
        info=json.loads((Path(root)/'.ready.json').read_text())
        if info['model']!=MODEL or info['format']!=FORMAT:return None
        for entry in info['files']:
            file=(Path(root)/entry['path']).resolve()
            if not file.is_relative_to(Path(root).resolve()) or not file.is_file() or file.stat().st_size!=entry['size']:return None
        return info if info['files'] else None
    except (OSError,ValueError,KeyError,TypeError):return None


def create_model(root,download=False):
    os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
    import onnxruntime
    onnxruntime.disable_telemetry_events()
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=MODEL,cache_dir=str(root),threads=1,providers=['CPUExecutionProvider'],local_files_only=not download)


def setup_model(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    if model_info(root):return
    model=create_model(root,True)
    list(model.embed(['Öffentliches Testbeispiel für lokale Sprachverarbeitung.']))
    weights=sorted(root.rglob('*.onnx'))
    if not weights:raise RuntimeError('Keine lokalen Modellgewichte gefunden')
    digest=hashlib.sha256((MODEL+FORMAT).encode())
    for weight in weights:
        with weight.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    files=[{'path':str(file.relative_to(root)),'size':file.stat().st_size} for file in root.rglob('*') if file.is_file() and file.suffix in ('.onnx','.json','.txt','.model') and not file.name.startswith('.')]
    marker=root/'.ready.tmp';marker.write_text(json.dumps({'model':MODEL,'format':FORMAT,'key':digest.hexdigest(),'files':files}))
    marker.replace(root/'.ready.json')


class SemanticSearch:
    def __init__(self,settings,store):
        self.settings,self.store=settings,store
        self.root=settings.data_dir/'models/semantic'
        self.model=None;self.client=None;self.key=None;self.collection=None
        self.lock=threading.RLock();self.query_cache=OrderedDict();self.reset_pending=False
        self.state={'enabled':settings.semantic_enabled,'ready':False,'indexed':0,'total':0,'remaining':0}

    def ensure(self):
        if not self.settings.semantic_enabled:return False
        info=model_info(self.root)
        if not info:
            self.state.update(ready=False,error='Lokales Suchmodell fehlt. Mit dem Installer oder scripts/setup_semantic.py bereitstellen.')
            return False
        if self.client is None:
            from qdrant_client import QdrantClient,models
            key=info['key'];collection='butler_'+key[:24]
            if self.model is None:self.model=create_model(self.root)
            model=self.model
            client=QdrantClient(path=str(self.settings.data_dir/'semantic-vectors'))
            try:
                if not client.collection_exists(collection):
                    self.reset_pending=True
                    client.create_collection(collection,vectors_config=models.VectorParams(size=DIMENSION,distance=models.Distance.COSINE))
                if self.reset_pending:
                    self.store.forget_semantic(key);self.reset_pending=False
            except Exception:
                client.close()
                raise
            # Publish only a completely initialized client; failed startup can retry cleanly.
            self.key,self.collection,self.model,self.client=key,collection,model,client
        self.state.update(ready=True,model=MODEL);self.state.pop('error',None)
        return True

    def point_id(self,chunk):return str(uuid.uuid5(uuid.NAMESPACE_URL,self.key+':'+str(chunk)))

    def index_batch(self):
        with self.lock:
            if not self.ensure():return 0
            from qdrant_client import models
            import numpy as np
            self.store.prepare_semantic_metadata()
            obsolete=self.store.obsolete_semantic(self.key)
            if obsolete:
                self.client.delete(self.collection,points_selector=models.PointIdsList(points=[self.point_id(value) for value in obsolete]))
                self.store.forget_semantic(self.key,obsolete)
            rows=self.store.pending_semantic(self.key)
            texts=[];ranges=[]
            for row in rows:
                body=message_text(row['body']);start=len(texts)
                # This compact multilingual model accepts 128 tokens. Cover the whole FTS chunk.
                for position in range(0,max(1,len(body)),300):texts.append(row['title'][:80]+'\n'+body[position:position+350])
                ranges.append((start,len(texts)))
            if texts:
                vectors=list(self.model.embed(texts,batch_size=16))
                points=[]
                for row,(start,end) in zip(rows,ranges):
                    vector=np.mean(vectors[start:end],axis=0);length=np.linalg.norm(vector)
                    if not length or not np.isfinite(vector).all():raise ValueError('Ungültiger lokaler Vektor')
                    points.append(models.PointStruct(id=self.point_id(row['chunk_id']),vector=(vector/length).tolist(),payload={'chunk_id':row['chunk_id'],'item_id':row['item_id'],'digest':row['digest'],'source':row['source']}))
                self.client.upsert(self.collection,points=points,wait=True)
                self.store.semantic_indexed(self.key,rows)
            self.state.update(self.store.semantic_counts(self.key),enabled=True,ready=True)
            self.state.pop('index_error',None);self.state.pop('index_error_code',None)
            return len(rows)

    def search(self,query,sources=None,limit=12):
        with self.lock:
            if not self.ensure():return []
            if sources is not None and not sources:return []
            from qdrant_client import models
            cache=self.query_cache.get(query)
            if cache and time.monotonic()-cache[0]<300:vector=cache[1]
            else:
                vector=list(self.model.embed([query[:1000]]))[0].tolist();self.query_cache[query]=(time.monotonic(),vector)
                while len(self.query_cache)>128:self.query_cache.popitem(last=False)
            filters=models.Filter(must=[models.FieldCondition(key='source',match=models.MatchAny(any=sources))]) if sources is not None else None
            points=self.client.query_points(self.collection,query=vector,query_filter=filters,limit=limit,score_threshold=0.25,with_payload=True).points
            hits=[];seen=set()
            for point in points:
                hit=self.store.semantic_evidence(point.payload,query)
                if hit and hit['item_id'] not in seen:
                    seen.add(hit['item_id']);hit['semantic_score']=point.score;hits.append(hit)
            return hits

    def close(self):
        with self.lock:
            if self.client:self.client.close();self.client=None
            self.model=None


def fuse_results(lexical,semantic,limit=8):
    scores={};records={}
    for weight,group in ((1.1,lexical),(1.0,semantic)):
        for rank,hit in enumerate(group):
            key=hit['item_id'];scores[key]=scores.get(key,0)+weight/(30+rank)
            records.setdefault(key,hit)
    ordered=sorted(records,key=lambda key:scores[key],reverse=True)
    return [records[key] for key in ordered[:limit]]
