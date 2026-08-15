local raw = redis.call('GET', KEYS[1])
local marker = redis.call('GET', KEYS[2])

local function current_count()
  if not raw then return 0 end
  local ok, data = pcall(cjson.decode, raw)
  if not ok then return -2 end
  return data['ref_count'] or 0
end

if marker == 'compensated' then
  return current_count()
end

if not marker then
  -- Tombstone the increment operation. This both proves there was nothing to
  -- undo and prevents a delayed/retried increment from running afterwards.
  redis.call('SET', KEYS[2], 'compensated', 'EX', 2592000)
  return current_count()
end

if not raw then return -1 end
local ok, data = pcall(cjson.decode, raw)
if not ok then return -2 end
local count = (data['ref_count'] or 1) - 1
if count <= 0 then
  redis.call('DEL', KEYS[1])
  count = 0
else
  data['ref_count'] = count
  redis.call('SET', KEYS[1], cjson.encode(data))
end
redis.call('SET', KEYS[2], 'compensated', 'EX', 2592000)
return count
