CREATE TABLE IF NOT EXISTS corpus (
  id INTEGER PRIMARY KEY,
  source TEXT,
  category TEXT,
  content TEXT
);

CREATE INDEX IF NOT EXISTS idx_corpus_source ON corpus(source);
CREATE INDEX IF NOT EXISTS idx_corpus_category ON corpus(category);

CREATE TABLE IF NOT EXISTS embeddings (
  corpus_id INTEGER PRIMARY KEY REFERENCES corpus(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vector BLOB NOT NULL
);
