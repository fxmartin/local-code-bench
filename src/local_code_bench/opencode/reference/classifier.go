// Package main is the canonical reference implementation of the OpenCode
// log-line classifier CLI. It is the ground truth the Task A black-box suite
// scores against, and lets the suite be verified with no live model.
//
// Behaviour:
//
//	classify <file>                 print counts per level (error, warn, info, unknown)
//	classify --json <file>          print the count table as a JSON object
//	classify --filter <level> <file>  print only the lines classified as <level>
//
// Severity rules (first match wins, case-sensitive):
//
//	contains "ERROR" or "FATAL" -> error
//	else contains "WARN"        -> warn
//	else contains "INFO"        -> info
//	else                        -> unknown
//
// Exit codes: 0 success, 1 file not found, 2 bad arguments.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

// levelOrder is the canonical reporting order for the count table.
var levelOrder = []string{"error", "warn", "info", "unknown"}

func classify(line string) string {
	switch {
	case strings.Contains(line, "ERROR") || strings.Contains(line, "FATAL"):
		return "error"
	case strings.Contains(line, "WARN"):
		return "warn"
	case strings.Contains(line, "INFO"):
		return "info"
	default:
		return "unknown"
	}
}

func isLevel(name string) bool {
	for _, level := range levelOrder {
		if name == level {
			return true
		}
	}
	return false
}

func main() {
	args := os.Args[1:]
	jsonMode := false
	filterLevel := ""
	file := ""
	haveFile := false

	for i := 0; i < len(args); i++ {
		arg := args[i]
		switch {
		case arg == "--json":
			jsonMode = true
		case arg == "--filter":
			if i+1 >= len(args) {
				fmt.Fprintln(os.Stderr, "error: --filter requires a level")
				os.Exit(2)
			}
			i++
			filterLevel = args[i]
		case strings.HasPrefix(arg, "--filter="):
			filterLevel = strings.TrimPrefix(arg, "--filter=")
		case strings.HasPrefix(arg, "-"):
			fmt.Fprintf(os.Stderr, "error: unknown flag %q\n", arg)
			os.Exit(2)
		default:
			if haveFile {
				fmt.Fprintln(os.Stderr, "error: too many arguments")
				os.Exit(2)
			}
			file = arg
			haveFile = true
		}
	}

	if filterLevel != "" && !isLevel(filterLevel) {
		fmt.Fprintf(os.Stderr, "error: unknown level %q\n", filterLevel)
		os.Exit(2)
	}

	if !haveFile {
		fmt.Fprintln(os.Stderr, "error: missing file argument")
		os.Exit(2)
	}

	handle, err := os.Open(file)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: cannot open %s\n", file)
		os.Exit(1)
	}
	defer handle.Close()

	var lines []string
	scanner := bufio.NewScanner(handle)
	for scanner.Scan() {
		lines = append(lines, scanner.Text())
	}

	levels := make([]string, len(lines))
	counts := map[string]int{"error": 0, "warn": 0, "info": 0, "unknown": 0}
	for i, line := range lines {
		levels[i] = classify(line)
		counts[levels[i]]++
	}

	switch {
	case jsonMode:
		encoded, err := json.Marshal(counts)
		if err != nil {
			fmt.Fprintln(os.Stderr, "error: failed to encode json")
			os.Exit(2)
		}
		fmt.Println(string(encoded))
	case filterLevel != "":
		for i, line := range lines {
			if levels[i] == filterLevel {
				fmt.Println(line)
			}
		}
	default:
		for _, level := range levelOrder {
			fmt.Printf("%s: %d\n", level, counts[level])
		}
	}
}
