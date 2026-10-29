-- Usage: osascript bodies.applescript <folder id> <start> <end> [plaintext]
-- Prints "<note id>\x1f<html body>" records separated by \x1e. Ids come from
-- the same range so callers pair bodies by id, not by position.
on run argv
	set folderId to item 1 of argv
	set startIndex to (item 2 of argv) as integer
	set endIndex to (item 3 of argv) as integer
	set wantPlain to (count of argv) > 3
	tell application "Notes"
		set theFolder to folder id folderId
		set noteCount to count of notes of theFolder
		if endIndex > noteCount then set endIndex to noteCount
		if startIndex > endIndex then return ""
		set noteIds to id of notes startIndex thru endIndex of theFolder
		if wantPlain then
			set noteBodies to plaintext of notes startIndex thru endIndex of theFolder
		else
			set noteBodies to body of notes startIndex thru endIndex of theFolder
		end if
	end tell
	set outParts to {}
	repeat with i from 1 to count of noteIds
		set end of outParts to (item i of noteIds) & (character id 31) & (item i of noteBodies)
	end repeat
	set AppleScript's text item delimiters to (character id 30)
	set output to outParts as text
	set AppleScript's text item delimiters to ""
	return output
end run
