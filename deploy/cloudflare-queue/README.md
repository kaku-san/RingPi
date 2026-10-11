This Worker stores one-shot doorbell commands in KV.
Set secret QUEUE_KEY and bind KV as Q.
GET with Authorization Bearer returns {"commands":[...]} and deletes them.
POST adds one command and fills id and expires_at.
Missing auth returns 401.
