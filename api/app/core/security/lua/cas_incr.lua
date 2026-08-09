local raw = redis.call('GET', KEYS[1])
local data
local previous_ttl = -2
local operation_state = redis.call('GET', KEYS[3])
if operation_state then
  -- A rollback tombstone must win over a delayed or retried increment.
  if operation_state == 'compensated' then return -3 end
  -- An idempotency marker without its CAS record is not a successful duplicate.
  -- The record is explicitly evictable, and silently returning zero here would
  -- leave ref-count and physical-usage state unreconstructed.
  if not raw then return -1 end
  local duplicate_ok, duplicate_data = pcall(cjson.decode, raw)
  if not duplicate_ok then return -2 end
  return duplicate_data['ref_count'] or 0
end
if not raw then
  if ARGV[1] then
    data = cjson.decode(ARGV[1])
    data['ref_count'] = 1
    -- Physical bytes are accounted by the storage facade at the actual
    -- object-store mutation boundary, never inferred from Redis metadata.
  else
    return -1
  end
else
  previous_ttl = redis.call('TTL', KEYS[1])
  local ok, decoded = pcall(cjson.decode, raw)
  if not ok then return -2 end
  data = decoded
  data['ref_count'] = (data['ref_count'] or 1) + 1
  if ARGV[1] then
    local arg_data = cjson.decode(ARGV[1])
    if arg_data['scanned_at'] then
      data['scanned_at'] = arg_data['scanned_at']
    end
    -- Keep original file_name if present, or update if missing
    if arg_data['file_name'] and not data['file_name'] then
      data['file_name'] = arg_data['file_name']
    end
    -- Also sync mime_type and size if they were missing
    if arg_data['mime_type'] and not data['mime_type'] then
      data['mime_type'] = arg_data['mime_type']
    end
    if arg_data['size'] and not data['size'] then
      data['size'] = arg_data['size']
    end
  end
end
redis.call('SET', KEYS[1], cjson.encode(data))
-- A staging TTL may be created or renewed, but must never be applied to a
-- durable shared record (TTL == -1).
if ARGV[2] and (not raw or previous_ttl >= 0) then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
redis.call('SET', KEYS[3], 'incremented', 'EX', 2592000)
return data['ref_count']
