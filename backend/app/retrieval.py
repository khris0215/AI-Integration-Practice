from langchain_community.document_loaders import DirectoryLoader, UnstructuredFileLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma

CHROMA_PATH = "./chroma_db"
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

def get_embeddings():
    return HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)

def create_vector_store():
    loader = DirectoryLoader("./data", glob="**/*", loader_cls=UnstructuredFileLoader, show_progress=True)
    docs = loader.load()
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = text_splitter.split_documents(docs)
    embeddings = get_embeddings()
    vectorstore = Chroma.from_documents(chunks, embeddings, persist_directory=CHROMA_PATH)
    vectorstore.persist()
    print(f"Stored {len(chunks)} chunks.")
    return vectorstore

def get_vector_store():
    embeddings = get_embeddings()
    return Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings)

def retrieve_relevant_chunks(query, k=5):
    db = get_vector_store()
    return db.similarity_search_with_relevance_scores(query, k=k)