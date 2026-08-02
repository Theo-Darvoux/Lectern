local raw = redis.call('GET', KEYS[1])
if redis.call('EXISTS', KEYS[2]) == 1 then
  if not raw then return 0 end
  local duplicate_ok, duplicate_data = pcall(cjson.decode, raw)
  if not duplicate_ok then return -2 end
  return duplicate_data['ref_count'] or 0
end
if not raw then return -1 end
local ok, data = pcall(cjson.decode, raw)
if not ok then return -2 end
local count = (data['ref_count'] or 1) - 1
if count <= 0 then
  -- Refcount release does not delete the S3 object. Physical usage remains
  -- charged until garbage collection deletes the object and reconciles usage.
  redis.call('DEL', KEYS[1])
  redis.call('SET', KEYS[2], '1', 'EX', 2592000)
  return 0
end
data['ref_count'] = count
redis.call('SET', KEYS[1], cjson.encode(data))
redis.call('SET', KEYS[2], '1', 'EX', 2592000)
return count
