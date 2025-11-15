# Converts the uevent file to logfmt.

BEGIN {
  FS = "="
  _prefixes[1] = "POWER_SUPPLY_"
  _result = ""
}

{
  k = $1
  v = $2

  # Keep only lines with key=value
  if (k == "" || v == "") next

  # Strip prefix if present
  for (i in _prefixes) {
    if (index(k, _prefixes[i]) == 1) {
      k = substr(k, length(_prefixes[i]) + 1)
      break
    }
  }

  # Normalize key
  k = tolower(k)

  # Skip duplicate keys
  if (seen[k]) next
  seen[k] = 1

  # Sanitize/clean value
  gsub(/\\/, "", v) # \ -> remove
  gsub(/"/, "'", v) # " -> '
  gsub(/^[[:space:]]+/, "", v)
  gsub(/[[:space:]]+$/, "", v)

  # Quote value
  if (v ~ /[[:space:]"']/) {
    v = "\"" v "\""
  }

  # Append in order
  _result = (_result == "") ? _result : _result " "
  _result = _result k "=" v

  # Capture needed numbers for later calculations
  if (k == "energy_now" && v ~ /^[0-9]+$/) energy_now = v + 0
  if (k == "energy_full" && v ~ /^[0-9]+$/) energy_full = v + 0
  if (k == "energy_full_design" && v ~ /^[0-9]+$/) energy_full_design = v + 0
}

END {
  # c_*: calculated fields

  # battery design_capacity: energy_full / energy_full_design * 100
  if (energy_full > 0 && energy_full_design > 0) {
    capacity = (energy_full / energy_full_design) * 100
    _result = _result " c_capacity=" sprintf("%.4f", capacity)
  }

  # battery percentage: energy_now / energy_full * 100
  if (energy_now > 0 && energy_full > 0) {
    percentage = (energy_now / energy_full) * 100
    _result = _result " c_percentage=" sprintf("%.4f", percentage)
  }

  print _result
}
