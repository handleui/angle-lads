# Slang detection — dictionary lookup only, no AI.
# On startup, loads all JSON files from dictionary/ into a single lookup dict.
# On every completed utterance, scans the transcript string for any matching key.
# If a match is found, returns the term and its definition.
