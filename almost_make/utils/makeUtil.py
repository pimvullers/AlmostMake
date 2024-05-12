#!/usr/bin/python3

# pylint: disable=missing-module-docstring
# pylint: disable=missing-class-docstring
# pylint: disable=missing-function-docstring
# pylint: disable=invalid-name

# Parses very simple Makefiles.
# Useful Resources:
#  - Chris Wellons' "A Tutorial on Portable Makefiles"
#    https://nullprogram.com/blog/2017/08/20/ Accessed August 22, 2020
#  - GNUMake:
#    https://www.gnu.org/software/make/manual/make.html Accessed August 22, 2020
#  - BSDMake:
#    http://khmere.com/freebsd_book/html/ch01.html Accessed August 22, 2020

import re
import sys
import os
import subprocess
import threading
import shlex
import glob
# from concurrent.futures import ThreadPoolExecutor # We are **not** using this because adding an
#                                                   # executor to the queue when in an executed thread can cause deadlock! See
#                                                   # https://docs.python.org/3/library/concurrent.futures.html#threadpoolexecutor

import almost_make.utils.macroUtil as macroUtility
import almost_make.utils.shellUtil.shellUtil as shellUtility
import almost_make.utils.shellUtil.runner as runner
import almost_make.utils.shellUtil.globber as globber
import almost_make.utils.shellUtil.escapeParser as escaper
import almost_make.utils.errorUtil as errorUtility

# Regular expressions
SPACE_CHARS = re.compile(r'\s+')
INCLUDE_DIRECTIVE_EXP = re.compile(
    r"^\s*(include|\.include|-include|sinclude)\s+")

# Targets that are used by this parser/should be ignored.
MAGIC_TARGETS = {
    ".POSIX",
    ".SUFFIXES"
}


class MakeUtil:
    currentFile = 'Makefile'
    currentLine = 0
    defaultMacros = {}
    recipeStartChar = '\t'
    silent = False
    macroCommands = {}
    maxJobs = 1
    currentJobs = 1  # Number of currently running jobs...
    jobLock = threading.Lock()
    pending = {}  # Set of pending jobs.
    justPrint = False  # Print commands, without evaluating.

    def __init__(self):
        # Functions for String Substitution and Analysis
        self.macroCommands["subst"] = self.makeCmdSubst
        self.macroCommands["patsubst"] = lambda argstring, macros: \
            self.makeCmdSubst(argstring, macros, patternBased=True)
        self.macroCommands["strip"] = lambda argstring, macros: \
            argstring.strip()
        self.macroCommands["findstring"] = lambda argstring, macros: \
            self.makeCmdFilter(argstring, macros, find=True)
        self.macroCommands["filter"] = self.makeCmdFilter
        self.macroCommands["filter-out"] = lambda argstring, macros: \
            self.makeCmdFilter(argstring, macros, exclude=True)
        self.macroCommands["sort"] = lambda argstring, macros: \
            " ".join(sorted(list(set(SPACE_CHARS.split(
                self.macroUtil.expandMacroUsages(argstring, macros))))))
        self.macroCommands["word"] = self.getWordOf
        self.macroCommands["wordlist"] = self.makeCmdWordList
        self.macroCommands["words"] = lambda argstring, macros: \
            str(len(SPACE_CHARS.split(
                self.macroUtil.expandMacroUsages(argstring, macros))))
        self.macroCommands["firstword"] = lambda argstring, macros: \
            self.getWordOf(argstring, macros, selectWord=0)
        self.macroCommands["lastword"] = lambda argstring, macros: \
            self.getWordOf(argstring, macros, selectWord=-1)

        # Functions for File Names
        self.macroCommands["dir"] = lambda argstring, macros: \
            " ".join([os.path.dirname(arg) for arg in SPACE_CHARS.split(
                self.macroUtil.expandMacroUsages(argstring, macros))])
        self.macroCommands["notdir"] = lambda argstring, macros: \
            " ".join([os.path.basename(arg) for arg in SPACE_CHARS.split(
                self.macroUtil.expandMacroUsages(argstring, macros))])
        self.macroCommands["suffix"] = lambda argstring, macros: \
            " ".join([suffix.strip() for suffix in re.findall(
                r"\.[^.\s]\s",
                self.macroUtil.expandMacroUsages(argstring, macros))])
        self.macroCommands["basename"] = lambda argstring, macros: \
            " ".join([basename.strip() for basename in re.split(
                r"\.[^.\s]\s",
                self.macroUtil.expandMacroUsages(argstring, macros))])
        self.macroCommands["addsuffix"] = lambda argstring, macros: \
            self.makeCmdAddFix(argstring, macros, cmd="addsuffix")
        self.macroCommands["addprefix"] = lambda argstring, macros: \
            self.makeCmdAddFix(argstring, macros, cmd="addprefix")
        self.macroCommands["join"] = self.makeCmdJoin
        self.macroCommands["wildcard"] = self.makeCmdWildcard
        self.macroCommands["realpath"] = self.makeCmdRealpath
        self.macroCommands["abspath"] = lambda argstring, macros: \
            " ".join([os.path.abspath(arg) for arg in SPACE_CHARS.split(
                self.macroUtil.expandMacroUsages(argstring, macros))])

        # Functions for Conditionals
        self.macroCommands["if"] = self.makeCmdIf
        self.macroCommands["or"] = self.makeCmdLogical
        self.macroCommands["and"] = lambda argstring, macros: \
            self.makeCmdLogical(argstring, macros, returnOnEmpty=True)
        self.macroCommands["intcmp"] = lambda argstring, macros: \
            self.makeCmdNotImplementedYet(argstring, macros, cmd="intcmp")

        # Functions that Control Make
        self.macroCommands["error"] = lambda argstring, macros: \
            self.makeCmdPrint(argstring, macros, cmd="error")
        self.macroCommands["warning"] = lambda argstring, macros: \
            self.makeCmdPrint(argstring, macros, cmd="warning")
        self.macroCommands["info"] = lambda argstring, macros: \
            self.makeCmdPrint(argstring, macros, cmd="info")

        # Other functions
        self.macroCommands["let"] = lambda argstring, macros: \
            self.makeCmdNotImplementedYet(argstring, macros, cmd="let")
        self.macroCommands["foreach"] = self.makeCmdForeach
        self.macroCommands["file"] = lambda argstring, macros: \
            self.makeCmdNotImplementedYet(argstring, macros, cmd="file")
        self.macroCommands["call"] = self.makeCmdCall
        self.macroCommands["value"] = lambda argstring, macros: \
            self.makeCmdNotImplementedYet(argstring, macros, cmd="value")
        self.macroCommands["eval"] = lambda argstring, macros: \
            self.makeCmdNotImplementedYet(argstring, macros, cmd="eval")
        self.macroCommands["origin"] = self.makeCmdOrigin
        self.macroCommands["flavor"] = lambda argstring, macros: \
            self.makeCmdNotImplementedYet(argstring, macros, cmd="flavor")
        # To-do: Use the built-in shell if specified...
        self.macroCommands["shell"] = lambda code, macros: \
            os.popen(self.macroUtil.expandMacroUsages(code, macros)).read() \
            .rstrip(' \n\r\t')
        self.macroCommands["guile"] = lambda argstring, macros: \
            self.makeCmdNotImplementedYet(argstring, macros, cmd="guile")

        self.errorUtil = errorUtility.ErrorUtil()
        self.macroUtil = macroUtility.MacroUtil()

        self.macroUtil.enableConditionals()  # ifeq, ifdef, etc.

        self.macroUtil.setMacroCommands(self.macroCommands)
        self.macroUtil.addMacroDefCondition(
            lambda line: not line.startswith(self.recipeStartChar))
        self.macroUtil.addLazyEvalCondition(
            lambda line: line.startswith(self.recipeStartChar))

        # Makefiles seem to generally expect undefined macros to expand to
        # nothing...
        self.setDefaultMacroExpansion("")

    def setStopOnError(self, stopOnErr):
        self.macroUtil.setStopOnError(stopOnErr)
        self.errorUtil.setStopOnError(stopOnErr)

    # Expand macros to [expansion] when undefined.
    # If [expansion] is None, display an error.
    def setDefaultMacroExpansion(self, expansion=None):
        self.macroUtil.setDefaultMacroExpansion(expansion)

    def setSilent(self, silent):
        self.silent = silent
        self.macroUtil.setSilent(silent)
        self.errorUtil.setSilent(silent)

    def setJustPrint(self, justPrint):
        self.justPrint = justPrint

    # Set the maximum number of threads used to evaluate targets.
    # Note, however, that use of a recursive build-system may cause more than
    # this number of jobs to be used/created.
    def setMaxJobs(self, maxJobs):
        self.maxJobs = maxJobs

    # Get a tuple.
    # First item: a map from target names
    #   to tuples of (dependencies, action)
    # Second item: A list of the targets
    #   with recipes.
    # This method parses the text of a makefile.
    def getTargetActions(self, content):
        lines = content.split('\n')
        lines.reverse()

        result = {}
        currentRecipe = []
        targetNames = []
        specialTargetNames = []

        for line in lines:
            if line.startswith(self.recipeStartChar):
                currentRecipe.append(line[len(self.recipeStartChar):])
                # Use len() in case we decide to
                # be less compliant and make it
                # more than a character.
            elif len(line.strip()) > 0:
                if ':' not in line:
                    if len(currentRecipe) > 0:
                        self.errorUtil.reportError(
                            "Pre-recipe line must contain separator! "
                            f"Line: {line}")
                    continue
                sepIndex = line.index(':')

                # Get what is generated (the targets).
                allGenerates = runner.shSplit(line[:sepIndex].strip(),
                                              {' ', '\t', '\n', ';'})
                allGenerates = runner.removeEqual(allGenerates, ';')
                allGenerates = runner.removeEmpty(allGenerates)

                # Get the dependencies (everything after the colon).
                preReqs = line[sepIndex + 1:].strip()
                dependsOn = runner.shSplit(preReqs, {' ', '\t', '\n', ';'})
                dependsOn = runner.removeEqual(dependsOn, ';')
                dependsOn = runner.removeEmpty(dependsOn)

                if self.isPatternSubstRecipe(line):
                    currentRecipe.reverse()
                    result[line] = ((allGenerates, dependsOn), currentRecipe)
                else:
                    for generates in allGenerates:
                        currentDeps = []
                        currentDeps.extend(dependsOn)

                        if generates in result:
                            oldDeps, oldRecipe = result[generates]
                            currentDeps.extend(oldDeps)
                            oldRecipe.reverse()
                            currentRecipe.extend(oldRecipe)

                        # Clean up & add to output.
                        outRecipe = [] + currentRecipe
                        outRecipe.reverse()
                        result[generates] = (currentDeps, outRecipe)

                        if generates.startswith('.'):
                            specialTargetNames.append(generates)
                        else:
                            targetNames.append(generates)
                currentRecipe = []
        # Move targets that start with a '.' to
        # the end...
        targetNames.reverse()
        targetNames.extend(specialTargetNames)
        return result, targetNames

    @staticmethod
    def isPatternSubstRecipe(line):
        if '%' not in line:
            return False
        parts = escaper.escapeSafeSplit(line, '%', '\\')

        return len(parts) > 1

    # Get a list of directories (including the current working directory)
    # from macros['VPATH']. Returns an array with one element, the current
    # working directory, if there is no 'VPATH' macro.
    @staticmethod
    def getSearchPath(macros):
        searchPath = [os.path.abspath('.')]

        if 'VPATH' not in macros:
            return searchPath

        vpath = macros['VPATH']

        # Split first by ';', then by ':', then finally,
        # try to split by space characters.
        splitOrder = [';', ':', ' ']
        split = []
        for char in splitOrder:
            split = escaper.escapeSafeSplit(vpath, char, True)
            split = runner.removeEmpty(split)

            if len(split) > 1:
                break

        searchPath.extend([os.path.normcase(part) for part in split])

        return searchPath

    # Find a file with relative path [givenPath]. If
    # VPATH is in macros, search each semi-colon, colon,
    # or space-separated entry for the file. Returns the
    # path to the file, or None, if the file does not exist.
    def findFile(self, givenPath, macros):
        givenPath = os.path.normcase(givenPath)
        searchPath = self.getSearchPath(macros)

        for part in searchPath:
            path = os.path.join(part, givenPath)

            if os.path.exists(path):
                return os.path.relpath(path)
        return None

    # Glob [text], but search [VPATH] for additional matches.
    def glob(self, text, macros):
        if 'VPATH' not in macros:
            return globber.glob(text, '.')

        searchPath = self.getSearchPath(macros)
        result = globber.glob(text, '.', [])
        text = os.path.normcase(text)

        for part in searchPath:
            result.extend(globber.glob(os.path.join(part, text), '.', []))

        # Act like system glob. If we didn't find anything,
        # return [ text ]
        if len(result) == 0:
            result = [text]

        return result

    # Glob all elements in arr, but not the first.
    def globArgs(self, arr, macros, excludeFirst=True):
        result = []
        isFirst = excludeFirst

        for part in arr:
            if not runner.isQuoted(part.strip()) and not isFirst:
                result.extend(self.glob(part, macros))
            else:
                result.append(part)
                isFirst = False

        return result

    # Generate a recipe for [target] and add it to [targets].
    # Returns True if there is now a recipe for [target] in [targets],
    #  False otherwise.
    def generateRecipeFor(self, target, targets, macros):
        if target in targets:
            return True

        generatedTarget = False
        potentialNewRules = []

        # Can we generate a recipe?
        for key in targets.keys():
            if self.isPatternSubstRecipe(key):
                details, rules = targets[key]
                generates, deps = details

                for targetTest in generates:
                    if targetTest == target:
                        # Replace all '%' symbols with wildcard symbols.
                        dependsOn = runner.shSplit(
                            self.patsubst("%", "*", dependsOn.join(" ")),
                            splitChars={' ', ';'})

                        potentialNewRules.append((dependsOn, rules))
                        continue
                    elif '%' not in targetTest:
                        continue

                    sepIndex = targetTest.index("%")
                    beforeContent = targetTest[:sepIndex]
                    afterContent = targetTest[sepIndex + 1:]

                    if target.startswith(beforeContent) and target.endswith(afterContent):
                        newKey = target
                        newReplacement = newKey[sepIndex:len(newKey) - len(afterContent)]
                        deps = " ".join(deps)
                        deps = escaper.escapeSafeSplit(deps, "%", "\\")
                        deps = newReplacement.join(deps)
                        deps = deps.split(" ")

                        potentialNewRules.append((deps, rules))
            elif key.startswith(".") and "." in key[1:]:
                shortKey = key[1:]  # Remove the first '.'
                parts = shortKey.split('.')  # NOT a regex.
                requires = '.' + parts[0].strip()
                creates = '.' + parts[1].strip()

                # Don't evaluate... The user probably didn't intend for us to
                # make a recipe from this.
                if len(parts) > 2:
                    continue

                if ".SUFFIXES" not in targets:
                    continue

                validSuffixes, _ = targets[".SUFFIXES"]

                # Are these valid suffixes?
                if creates not in validSuffixes \
                        or requires not in validSuffixes:
                    continue

                # Does it fit the current target?
                if target.endswith(creates):
                    deps, rules = targets[key]

                    newDeps = [dep for dep in deps if dep != '']
                    withoutExtension = target[: - len(creates)]

                    newDeps.append(withoutExtension + requires)

                    potentialNewRules.append((newDeps, rules))
            # Is it the same thing, just formatted differently?
            elif os.path.abspath(key) == os.path.abspath(target):
                rules, deps = targets[key]
                potentialNewRules.append((rules, deps))

        fewestUngeneratableDeps = None
        for deps, rules in potentialNewRules:
            unsatisfiableCount = 0

            # Expand wildcard expressions.
            globbedDeps = self.globArgs(runner.removeEmpty(deps), macros,
                                        False)

            # How many dependencies are we unable to satisfy?
            for dep in globbedDeps:
                phony = self.isPhony(dep, targets)
                exists = self.findFile(dep, macros)
                if not phony and not exists:
                    unsatisfiableCount += 1

            # If we don't have a recipe,
            # or the current recipe looks better than what we already have.
            if fewestUngeneratableDeps is None or \
                    unsatisfiableCount < fewestUngeneratableDeps:
                targets[target] = (deps, rules)
                generatedTarget = True
                fewestUngeneratableDeps = unsatisfiableCount
        return generatedTarget

    # Return True iff [target] is not a "phony" target
    # (as declared by .PHONY). [targets] is the list of all
    # targets.
    @staticmethod
    def isPhony(target, targets):
        if ".PHONY" not in targets:
            return False

        phonyTargets, _ = targets['.PHONY']
        return target in phonyTargets or target in MAGIC_TARGETS

    # Get whether [target] needs to be (re)generated. If necessary,
    # creates a rule for [target] and adds it to [targets].
    def prepareGenerateTarget(self, target, targets, macros, visitingSet=None):
        target = target.strip()

        if visitingSet is None:
            visitingSet = set()

        if target in visitingSet:  # Circular dependency?
            self.errorUtil.logWarning(
                f"Circular dependency involving {target}!!!")

            # Just return whether it exists or not.
            return self.findFile(target, macros) is None

        if target not in targets:
            self.generateRecipeFor(target, targets, macros)

        targetPath = self.findFile(target, macros)
        selfExists = targetPath is not None

        if target not in targets:
            if selfExists:
                return False
            else:
                # This is an error! We need to generate the target, but
                # there is no rule for it!
                self.errorUtil.reportError(f"No rule to make {target}.")
                return False  # If still running, we can't generate this.

        deps, _ = targets[target]
        # Glob the set of dependencies.
        deps = self.globArgs(runner.removeEmpty(deps), macros, False)

        if selfExists:
            selfMTime = os.path.getmtime(targetPath)
        else:
            return True

        if self.isPhony(target, targets):
            return True

        for dep in deps:
            if self.isPhony(dep, targets):
                return True

            pathToOther = self.findFile(dep, macros)

            # If it doesn't exist...
            if pathToOther is None:
                return True

            # If we're older than it...
            if selfMTime < os.path.getmtime(pathToOther):
                return True

            visitingSet.add(target)
            needGenerateDep = self.prepareGenerateTarget(
                dep, targets, macros, visitingSet)
            visitingSet.remove(target)

            if needGenerateDep:
                return True
        return False

    # Generate [target] if necessary (i.e. run recipes to create). Returns
    # True if generated, False if not necessary.
    def satisfyDependencies(self, target, targets, macros):
        target = target.strip()

        if not self.prepareGenerateTarget(target, targets, macros):
            return False

        targetPath = self.findFile(target, macros)

        deps, commands = targets[target]
        deps = self.globArgs(runner.removeEmpty(deps), macros, False)  # Glob the set of dependencies.

        depPaths = []

        for dep in deps:
            if self.isPhony(dep, targets):
                depPaths.append(dep)
            else:
                depPaths.append(self.findFile(dep, macros) or dep)

        pendingJobs = []

        for dep in deps:
            # print("Checking dep %s; %s" % (dep, str(needGenerate(dep))))
            if dep.strip() != "" and self.prepareGenerateTarget(
                    dep, targets, macros):
                self.jobLock.acquire()
                if self.currentJobs < self.maxJobs and dep not in self.pending:
                    self.currentJobs += 1
                    self.jobLock.release()

                    self.pending[dep] = threading.Thread(
                        target=self.satisfyDependencies,
                        args=(dep, targets, macros))

                    pendingJobs.append(dep)
                else:
                    self.jobLock.release()
                    self.satisfyDependencies(dep, targets, macros)

        for job in pendingJobs:
            self.pending[job].start()

        # Wait for all pending jobs to complete.
        for job in pendingJobs:
            self.pending[job].join()
            self.pending[job] = None

            self.jobLock.acquire()
            self.currentJobs -= 1
            self.jobLock.release()

        # Here, we know that
        # (1) all dependencies are satisfied
        # (2) we need to run each command in recipe.
        # Define several macros the client will expect here:
        macros["@"] = targetPath or target
        macros["^"] = " ".join(depPaths)
        if len(deps) >= 1:
            macros["<"] = depPaths[0]

        for command in commands:
            command = self.macroUtil.expandMacroUsages(command, macros).strip()
            if command.startswith("@"):
                command = command[1:]
            elif not self.silent:
                print(command)
                # Flush output so that it's not out of order when there's no TTY.
                sys.stdout.flush()
            haltOnFail = not command.startswith("-")
            if command.startswith("-"):
                command = command[1:]

            origDir = os.getcwd()

            try:
                status = 0

                if self.justPrint:
                    print(command)
                elif "_BUILTIN_SHELL" not in macros:
                    status = subprocess.run(command, shell=True, check=True).returncode
                else:
                    defaultFlags = []

                    if "_SYSTEM_SHELL_PIPES" in macros:
                        defaultFlags.append(runner.USE_SYSTEM_PIPE)

                    status, _ = shellUtility.evalScript(command, self.macroUtil, macros, defaultFlags=defaultFlags)

                if status != 0 and haltOnFail:
                    self.errorUtil.reportError("Command %s exited with non-zero exit status, %s." % (command, str(status)))
            except Exception as e:
                if haltOnFail:  # e.g. -rm foo should be silent even if it cannot remove foo.
                    self.errorUtil.reportError("Unable to run command:\n    ``%s``. \n\n  Message:\n%s" % (command, str(e)))
            finally:
                # We should not switch directories, regardless of the command's result.
                # Some platforms (e.g. a-Shell) do not reset the cwd after child processes exit.
                if os.getcwd() != origDir:
                    os.chdir(origDir)
        return True

    # Handle all .include and include directives, as well as any conditionals.
    def handleIncludes(self, contents, macros):
        lines = self.macroUtil.getLines(contents)
        lines.reverse()

        newLines = []
        inRecipe = False

        for line in lines:
            if line.startswith(self.recipeStartChar):
                inRecipe = True
            elif inRecipe:
                inRecipe = False
            elif INCLUDE_DIRECTIVE_EXP.search(line) is not None:
                line = self.macroUtil.expandMacroUsages(line, macros)

                parts = runner.shSplit(line)
                command = parts[0].strip()

                parts = self.globArgs(parts, macros)  # Glob all, except the first...
                parts = parts[1:]  # Remove leading include...
                ignoreError = False

                # Safe including?
                if command.startswith('-') or command.startswith('s'):
                    ignoreError = True

                for fileName in parts:
                    fileName = runner.stripQuotes(fileName)

                    if not os.path.exists(fileName):
                        foundName = self.findFile(fileName, macros)

                        if foundName is not None:
                            fileName = foundName

                    if not os.path.exists(fileName):
                        if ignoreError:
                            continue

                        self.errorUtil.reportError("File %s does not exist. Context: %s" % (fileName, line))
                        return contents, macros

                    if not os.path.isfile(fileName):
                        if ignoreError:
                            continue

                        self.errorUtil.reportError("%s is not a file! Context: %s" % (fileName, line))
                        return contents, macros

                    try:
                        with open(fileName, 'r') as file:
                            contents = file.read().split('\n')
                            contents.reverse()  # We're reading in reverse, so write in reverse.

                            newLines.extend(contents)
                        continue
                    except IOError as ex:
                        if ignoreError:
                            continue

                        self.errorUtil.reportError("Unable to open %s: %s. Context: %s" % (fileName, str(ex), line))
                        return contents, macros
            newLines.append(line)

        newLines.reverse()

        return self.macroUtil.expandAndDefineMacros("\n".join(newLines), macros)

    # Macro commands.

    # Example: $(subst foo,bar,foobar baz) -> barbar baz
    # See https://www.gnu.org/software/make/manual/html_node/Syntax-of-Functions.html#Syntax-of-Functions
    #     and https://www.gnu.org/software/make/manual/html_node/Text-Functions.html
    def makeCmdSubst(self, argstring, macros, patternBased=False):
        args = self.macroUtil.argumentSplit(argstring)

        if len(args) < 3:
            self.errorUtil.reportError("Too few arguments given to subst function. Arguments: %s" % ','.join(args))

        firstThreeArgs = args[:3]
        firstThreeArgs[2] = ','.join(args[2:])
        args = firstThreeArgs

        replaceText = self.macroUtil.expandMacroUsages(args[0], macros)
        replaceWith = self.macroUtil.expandMacroUsages(args[1], macros)
        text = self.macroUtil.expandMacroUsages(args[2], macros)

        if not patternBased:
            return re.sub(re.escape(replaceText), replaceWith, text)
        else:  # Using $(patsubst pattern,replacement,text)
            return self.patsubst(replaceText, replaceWith, text)

    # Example: $(word 3, get the third word) -> third
    # If the word with the given index does not exist, return
    # the empty string.
    # Ref: https://www.gnu.org/software/make/manual/html_node/Text-Functions.html#index-word
    #
    # Note:
    #    If [selectWord] is not None, then attempt to select the specified word.
    #    For example, getWordOf(..., selectWord=-1) selects the last word in argstring.
    #    If selectWord is None, then determine the word to select from the contents of
    #    [argstring].
    def getWordOf(self, argstring, macros, selectWord=None):
        selectIndex = selectWord
        argText = argstring.strip()

        if selectIndex is None:
            args = self.macroUtil.argumentSplit(argstring)

            if len(args) <= 1:
                self.errorUtil.reportError(
                    "Not enough arguments to word selection macro. Context: %s" % argstring
                )

                return ""

            selectIndexText = self.macroUtil.expandMacroUsages(args[0], macros)

            try:
                # From argstring (one-indexed) => string indicies (zero-indicies).
                selectIndex = int(selectIndexText) - 1
                argText = ','.join(args[1:]).strip()
            except ValueError:
                self.errorUtil.reportError(
                    "First argument to word selection macros must be an integer. Context: %s"
                        % argstring
                )
                return ""

        argText = self.macroUtil.expandMacroUsages(argText, macros)
        words = SPACE_CHARS.split(argText)

        # TODO: Is there a way to do this with if-statements?
        try:
            return words[selectIndex]
        except IndexError:
            return ""

    def makeCmdWordList(self, argstring, macros):
        args = self.macroUtil.argumentSplit(argstring)

        if len(args) <= 2:
            self.errorUtil.reportError(
                f"Not enough arguments to wordlist macro. Context: {argstring}"
            )
            return ""

        selectStartText = self.macroUtil.expandMacroUsages(args[0], macros)
        selectStopText = self.macroUtil.expandMacroUsages(args[1], macros)

        try:
            # From argstring (one-indexed) => string indices (zero-indexed).
            selectStart = int(selectStartText) - 1
            selectStop = int(selectStopText) - 1
            argText = ','.join(args[2:]).strip()
        except ValueError:
            self.errorUtil.reportError(
                "First arguments to wordlist macros must be an integer."
                f"Context: {argstring}"
            )
            return ""

        argText = self.macroUtil.expandMacroUsages(argText, macros)
        words = SPACE_CHARS.split(argText)

        if selectStart > len(words):
            return ""

        selectStop = min(selectStop, len(words))
        return " ".join(words[selectStart:selectStop+1])

    # Format: $(if condition,then-part[,else-part])
    # Example: $(if ,a,b) -> b
    # Example: $(if c,a,b) -> a
    # See https://www.gnu.org/software/make/manual/html_node/Syntax-of-Functions.html#Syntax-of-Functions
    #     and https://www.gnu.org/software/make/manual/html_node/Conditional-Functions.html
    def makeCmdIf(self, argstring, macros):
        args = self.macroUtil.argumentSplit(argstring)

        if not (len(args) == 2 or len(args) == 3):
            self.errorUtil.reportError(
                "Incorrect number of arguments given to if function. "
                f"Arguments: {argstring}")

        cond = self.macroUtil.expandMacroUsages(args[0], macros).strip()
        if cond != "":
            return args[1]
        else:
            return "" if len(args) == 2 else args[2]

    def makeCmdLogical(self, argstring, macros, returnOnEmpty=False):
        args = self.macroUtil.argumentSplit(argstring)

        result = ""
        for condition in args:
            expanded_condition = self.macroUtil.expandMacroUsages(
                condition, macros)
            if expanded_condition == "":
                if returnOnEmpty:
                    return ""
            else:
                if not returnOnEmpty:
                    return expanded_condition
            result = expanded_condition

        return result

    # Format:
    #  - $(filter pattern...,text)
    #  - $(filter-out pattern...,text)
    #  - $(findstring pattern, text)
    # Example: $(filter a b,a b c) -> a b
    # Example: $(filter-out a b,a b c) -> c
    # Example: $(findstring a,a b c) -> a
    # Example: $(findstring a,b c) -> ""
    # See https://www.gnu.org/software/make/manual/html_node/Syntax-of-Functions.html#Syntax-of-Functions
    #     and https://www.gnu.org/software/make/manual/html_node/Text-Functions.html
    def makeCmdFilter(self, argstring, macros, exclude=False, find=False):
        args = self.macroUtil.argumentSplit(argstring)

        if not len(args) == 2:
            self.errorUtil.reportError(
                "Incorrect number of arguments given to filter function. "
                f"Arguments: {argstring}")

        if '%' in args[0]:
            self.errorUtil.reportError(
                "Patterns are not yet supported for filter function. "
                f"Filters: {args[0]}")

        patterns = SPACE_CHARS.split(
            self.macroUtil.expandMacroUsages(args[0], macros))
        text = SPACE_CHARS.split(
            self.macroUtil.expandMacroUsages(args[1], macros))

        match = []
        mismatch = []
        for word in text:
            if word in patterns:
                match.append(word)
            else:
                mismatch.append(word)

        if find:
            return args[0] if len(match) > 0 else ""
        else:
            return " ".join(mismatch if exclude else match)

    def makeCmdNotImplementedYet(self, argstring, macros, cmd):
        raise NotImplementedError(f"$({cmd} ...) not yet implemented")

    def makeCmdJoin(self, argstring, macros):
        args = self.macroUtil.argumentSplit(argstring)
        a = SPACE_CHARS.split(args[0])
        b = SPACE_CHARS.split(args[1])

        result = []
        for index in range(0, min(len(a), len(b))):
            result.append(f"{a[index]}{b[index]}")

        if len(a) > len(b):
            result += a[len(b):]
        else:
            result += b[len(a):]

        return " ".join(result)

    def makeCmdAddFix(self, argstring, macros, cmd):
        args = self.macroUtil.argumentSplit(argstring)
        fix = self.macroUtil.expandMacroUsages(args[0], macros)
        if cmd == "addsuffix":
            suffix = fix
            prefix = ""
        else:
            suffix = ""
            prefix = fix
        result = []
        for entry in SPACE_CHARS.split(
                self.macroUtil.expandMacroUsages(args[1], macros)):
            result.append(f"{prefix}{entry}{suffix}")

        return " ".join(result)

    def makeCmdPrint(self, argstring, macros, cmd):
        out = sys.stdout
        prefix = ""
        suffix = ""

        if cmd != "info":
            out = sys.stderr
            prefix = f"{self.currentFile}:{self.currentLine + 1}: "
        if cmd == "error":
            prefix += "*** "
            suffix = ".  Stop."

        text = self.macroUtil.expandMacroUsages(argstring, macros)
        print(f"{prefix}{text}{suffix}", file=out)

        if cmd == "error":
            exit(2)
        else:
            return ""

    # https://www.gnu.org/software/make/manual/html_node/Foreach-Function.html
    def makeCmdForeach(self, argstring, macros):
        args = self.macroUtil.argumentSplit(argstring)

        if not len(args) == 3:
            self.errorUtil.reportError(
                "Incorrect number of arguments given to foreach function. "
                f"Arguments: {argstring}")

        varname = self.macroUtil.expandMacroUsages(args[0], macros)
        entries = SPACE_CHARS.split(
            self.macroUtil.expandMacroUsages(args[1], macros))
        text = args[2]

        result = []
        for entry in entries:
            each_macros = macros.copy()
            each_macros[varname] = entry
            result.append(self.macroUtil.expandMacroUsages(text, each_macros))

        return " ".join(result)

    # https://www.gnu.org/software/make/manual/html_node/Call-Function.html
    def makeCmdCall(self, argstring, macros):
        args = self.macroUtil.argumentSplit(argstring)

        varname = self.macroUtil.expandMacroUsages(args[0], macros).strip()
        call_macros = macros.copy()
        for index, arg in enumerate(args):
            call_macros[str(index)] = arg

        if varname in self.macroUtil.macroCommands:
            return self.macroUtil.macroCommands[varname](",".join(args[1:]),
                                                         call_macros)
        else:
            return self.macroUtil.expandMacroUsages(
                macros[varname], call_macros)

    # https://www.gnu.org/software/make/manual/html_node/Wildcard-Function.html
    def makeCmdWildcard(self, argstring, macros):
        patterns = SPACE_CHARS.split(
            self.macroUtil.expandMacroUsages(argstring, macros))

        result = []
        for pattern in patterns:
            result.extend(glob.glob(pattern))
        return " ".join([shlex.quote(part) for part in result])

    # https://www.gnu.org/software/make/manual/html_node/File-Name-Functions.html
    def makeCmdRealpath(self, argstring, macros):
        paths = SPACE_CHARS.split(
            self.macroUtil.expandMacroUsages(argstring, macros))

        result = []
        for p in paths:
            try:
                result.append(os.path.realpath(p, strict=True))
            except OSError:
                pass

        return " ".join(result)

    # https://www.gnu.org/software/make/manual/html_node/Origin-Function.html
    def makeCmdOrigin(self, argstring, macros):
        text = self.macroUtil.expandMacroUsages(argstring, macros)

        if text not in macros:
            return "undefined"
        elif text in self.defaultMacros:
            return "default"
        elif text in self.macroUtil.getDefaultMacros():
            return "environment"
        else:
            return "file"

    # Replace all patterns defined by replaceText with replaceWith
    # in text.
    @staticmethod
    def patsubst(replaceText, replaceWith, text):
        words = SPACE_CHARS.split(text.strip())
        result = []

        pattern = escaper.escapeSafeSplit(replaceText, '%', '\\')
        replaceWith = escaper.escapeSafeSplit(replaceWith, '%', '\\')

        replaceAll = False
        replaceExact = False
        staticReplace = False

        if len(pattern) == 1:
            replaceAll = pattern == ''
            replaceExact = pattern[0]

        if len(replaceWith) <= 1:
            staticReplace = True

        while len(pattern) < 2:
            pattern.append('')
        while len(replaceWith) < 2:
            replaceWith.append('')

        pattern[1] = '%'.join(pattern[1:])
        replaceWith[1] = '%'.join(replaceWith[1:])

        for word in words:
            if not replaceExact and (replaceAll or word.startswith(pattern[0]) and word.endswith(pattern[1])):
                if not staticReplace:
                    result.append(replaceWith[0] + word[len(pattern[0]):-len(pattern[1])] + replaceWith[1])
                else:
                    result.append(replaceWith[0])
            elif replaceExact == word.strip():
                result.append('%'.join(runner.removeEmpty(replaceWith)))
            else:
                result.append(word)

        return " ".join(runner.removeEmpty(result))

    # Intended for use directly by clients:

    # Run commands specified to generate
    # dependencies of target by the contents
    # of the makefile given in contents.
    def runMakefile(self, contents, target='',
                    defaultMacros=None,
                    overrideMacros=None, file="Makefile"):
        if defaultMacros is None:
            defaultMacros = {"MAKE": "almake"}
        self.defaultMacros = defaultMacros.copy()
        self.currentFile = file
        contents, macros = self.macroUtil.expandAndDefineMacros(
            contents, defaultMacros, self)
        contents, macros = self.handleIncludes(contents, macros)
        targetRecipes, targets = self.getTargetActions(contents)

        if target == '' and len(targets) > 0:
            target = targets[0]

        # Fill override macros.
        if overrideMacros is not None:
            for macroName in overrideMacros:
                macros[macroName] = overrideMacros[macroName]

        satisfied = self.satisfyDependencies(target, targetRecipes, macros)

        if not satisfied and not self.silent:
            print("Nothing to be done for target ``%s``." % target)

        return satisfied, macros
