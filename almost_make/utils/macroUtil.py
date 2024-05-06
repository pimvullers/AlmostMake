#!/usr/bin/python3

# pylint: disable=missing-module-docstring
# pylint: disable=missing-class-docstring
# pylint: disable=missing-function-docstring
# pylint: disable=invalid-name
# Macro parsing utilities.

import re
import os
import almost_make.utils.shellUtil.runner as runner
import almost_make.utils.errorUtil as errorUtil

# Regular expressions:
MACRO_NAME_CHAR_EXP = r"[a-zA-Z0-9_]"
MACRO_NAME_CHAR_RE = re.compile(MACRO_NAME_CHAR_EXP)
MACRO_SET_EXP = r"\s*([:+?]?)=\s*"
MACRO_SET_RE = re.compile(MACRO_SET_EXP)
IS_MACRO_DEF_RE = re.compile(f"^{MACRO_NAME_CHAR_EXP}+{MACRO_SET_EXP}.*")
IS_MACRO_INVOKE_RE = re.compile(f".*[$][({{]?{MACRO_NAME_CHAR_EXP}+[)}}]?")
SPACE_CHARS = re.compile(r"\s")

CONDITIONAL_START = re.compile(r"^\s*(ifeq|ifneq|ifdef|ifndef)(?:\s|$)")
CONDITIONAL_ELSE = re.compile(r"^\s*(else)(?:\s|$)")
CONDITIONAL_STOP = re.compile(r"^\s*(endif)(?:\s|$)")

# Constant(s)
COMMENT_CHAR = '#'


class MacroUtil:
    # All commands executable as $(name arg1, arg2, ...)
    macroCommands = {}
    # A list of additional preconditions for the definition of a macro.
    definitionConditions = []
    # Don't expand macros on a line when in define & expand mode if any of
    # these conditions are true.
    lazyEvalConditions = []
    conditionals = False
    errorLogger = errorUtil.ErrorUtil()
    expandUndefinedMacrosTo = None

    def setStopOnError(self, stopOnErr):
        self.errorLogger.setStopOnError(stopOnErr)

    def setSilent(self, silent):
        self.errorLogger.setSilent(silent)

    # By default, an error is thrown when we attempt to expand undefined
    # macros. Instead, expand these macros to [expansion].
    def setDefaultMacroExpansion(self, expansion):
        self.expandUndefinedMacrosTo = expansion

    def setMacroCommands(self, commands):
        self.macroCommands = commands

    def addMacroDefCondition(self, condition):
        self.definitionConditions.append(condition)

    # Skip expanding macros on a line if condition holds. Applies only to
    # expandAndDefineMacros.
    def addLazyEvalCondition(self, condition):
        self.lazyEvalConditions.append(condition)

    # Turn on conditional support!
    def enableConditionals(self):
        self.conditionals = True

    # Get whether expandAndDefineMacros should
    # evaluate the contents of a line, or allow it to
    # be done later. Add conditions via addLazyEvalCondition.
    def shouldLazyEval(self, text):
        for condition in self.lazyEvalConditions:
            if condition(text):
                return True
        return False

    # Get if [text] defines a macro.
    def isMacroDef(self, text):
        if IS_MACRO_DEF_RE.match(text) is None:
            return False
        for condition in self.definitionConditions:
            if not condition(text):
                return False
        return True

    # Get whether [text] defines a macro with value that should be exported to
    # the environment.
    def isMacroExport(self, text):
        if not text.startswith("export "):
            return False
        return self.isMacroDef(text[len("export "):].strip())

    # Get if [text] syntactically invokes a macro.
    @staticmethod
    def isMacroInvoke(text):
        return IS_MACRO_INVOKE_RE.match(text) is not None

    # Get if [text] is a conditional statement.
    def isConditional(self, text):
        # Lazy evaluation for this line? Skip it.
        if self.shouldLazyEval(text):
            return False

        return CONDITIONAL_START.match(text) or CONDITIONAL_STOP.match(text) \
            or CONDITIONAL_ELSE.match(text)

    # Get the name of the conditional in [text], or None, if no conditionals
    # are defined.
    def getConditional(self, text):
        if not self.isConditional(text):
            return None

        startMatch = CONDITIONAL_START.match(text)
        elseMatch = CONDITIONAL_ELSE.match(text)
        endMatch = CONDITIONAL_STOP.match(text)

        # If there was a match, there should be at least one group.
        return (startMatch or elseMatch or endMatch).group(1)

    # Return [ifBranch] or [elseBranch] based on the contents of
    # [conditionalContent]. CONDITIONAL_START.match([conditionalContent])
    # should not be None. Do not expand and define macros in the chosen branch.
    def evaluateIf(self, conditionalContent, ifBranch, elseBranch, macros):
        # We should always be given a valid starting conditional.
        assert self.isConditional(conditionalContent)
#        print('----------')
#        print(conditionalContent + ";;" + ifBranch + ";;" + str(elseBranch))
#        print('----------')

        conditionalContent = conditionalContent.lstrip()
        conditional = CONDITIONAL_START.match(conditionalContent).group(1)
        argText = conditionalContent[len(conditional) + 1:].strip()
        argText = self.expandMacroUsages(argText, macros).strip()
        choseIfBranch = True

        if conditional == 'ifdef':
            choseIfBranch = argText.strip() in macros
        elif conditional == 'ifndef':
            choseIfBranch = not argText.strip() in macros
        else:  # Binary conditionals.
            args = runner.shSplit(argText, {',', ' ', '\t', '(', ')'})
            args = runner.removeEqual(runner.unwrapParens(args), ',')

            # If ifeq (foo, bar baz) syntax,
            if len(args) > 2 and argText.strip()[0] == '(':
                args = runner.shSplit(runner.unwrapParens(argText), {','})

                if len(args) == 3:
                    if args[1] != ",":
                        self.errorLogger.reportError(
                            f"Binary conditional {conditional} uses "
                            "ifeq (A, B) syntax, but does not contain a"
                            "separating comma!")
                    args = [args[0], args[2]]

            # shSplit removes empty elements. Add in an empty element if
            # necessary.
            while len(args) < 2:
                args.append('')

            if len(args) != 2:
                self.errorLogger.reportError(
                    """Binary conditional %s has %s arguments!
Context: %s, so,
%s
%s
else
%s
endif
--------
From %s, parsed arguments: %s""" % (
                        str(conditional),
                        str(len(args)),
                        str(conditionalContent),
                        str(conditional) + " " + str(argText),
                        str(ifBranch),
                        str(elseBranch),
                        str(argText),
                        str(args)))

            if conditional == 'ifeq':
                choseIfBranch = args[0] == args[1]
            elif conditional == 'ifneq':
                choseIfBranch = args[0] != args[1]
            else:
                self.errorLogger.reportError(
                    f"Unknown conditional {conditional}. "
                    f"Context: {conditionalContent}.")

        if choseIfBranch:
            return ifBranch
        return elseBranch or ''  # elseBranch can be None...

    # Get a list of suggested default macros from the environment
    @staticmethod
    def getDefaultMacros():
        result = {}

        for name in os.environ:
            result[name] = os.environ[name]

        return result

    # Split content by lines, but paying attention to escaped newline
    # characters.
    @staticmethod
    def getLines(content):
        result = []
        escapeCharLast = False
        buff = ''

        for c in content:
            if c == '\\' and not escapeCharLast:
                escapeCharLast = True
            elif escapeCharLast and c != '\n':
                buff += '\\' + c
                escapeCharLast = False
            elif escapeCharLast and c == '\n':
                buff += ' '
                escapeCharLast = False
            elif c == '\n':
                result.append(buff)
                buff = ''
            else:
                escapeCharLast = False
                buff += c

        result.append(buff)
        return result

    # Remove comments from line as defined by COMMENT_CHAR
    def stripComments(self, line, force=False):
        singleLevel = {'"': False, "\'": False}
        inSomeSingleLevel = False
        multiLevelOpen = {'(': 0, '{': 0}
        multiLevelClose = {')': '(', '}': '{'}
        escaped = False
        trimToIndex = 0

        for c in line:
            if c in singleLevel and not escaped:
                if not inSomeSingleLevel:
                    inSomeSingleLevel = True
                    singleLevel[c] = True
                elif singleLevel[c]:
                    inSomeSingleLevel = False
                    singleLevel[c] = False
            elif c == '\\' and not escaped:
                escaped = True
            elif c == '\\' and escaped:
                escaped = False
            elif c in multiLevelOpen and not escaped and not inSomeSingleLevel:
                multiLevelOpen[c] += 1
            elif c in multiLevelClose and not escaped and \
                    not inSomeSingleLevel:
                bracketPairChar = multiLevelClose[c]
                if multiLevelOpen[bracketPairChar] == 0:
                    self.errorLogger.reportError(
                        f"Parentheses mismatch on line with content: {line}")
                else:
                    multiLevelOpen[bracketPairChar] -= 1
            elif c == COMMENT_CHAR and not escaped and not inSomeSingleLevel \
                    and (not self.shouldLazyEval(line) or force):
                break
            else:
                escaped = False
            trimToIndex = trimToIndex + 1
        return line[:trimToIndex]

    def argumentSplit(self, argstring):
        buff = ''
        argBuff = ''
        parenLevel = 0
        inMacro = False

        args = []
        for c in argstring:
            if c == ',' and not inMacro:
                args.append(argBuff)
                argBuff = ''
            else:
                argBuff += c
                if c == '$' and not inMacro and parenLevel == 0:
                    buff += c
                    inMacro = True
                elif c == '$' and parenLevel == 0 and inMacro and buff == '':
                    inMacro = False
                elif (c == '(' or c == '{') and inMacro:
                    parenLevel += 1

                    if parenLevel > 1:
                        buff += c
                elif (c == ')' or c == '}') and inMacro:
                    parenLevel -= 1

                    if parenLevel == 0:
                        inMacro = False
                    else:
                        buff += c
                elif inMacro and parenLevel == 0 and \
                        not MACRO_NAME_CHAR_RE.match(c):
                    inMacro = False
                else:
                    buff += c

        if parenLevel > 0:
            self.errorLogger.reportError(f"Unclosed parenthesis: {argstring}")

        args.append(argBuff)
        return args

    # Expand usages of [macros] in [line]. Make no definitions and expand
    # regardless of lazyEvalConditions.
    def expandMacroUsages(self, line, macros):
        expanded = ''
        buff = ''
        afterBuff = ''
        parenLevel = 0
        inMacro = False
        buffFromMacro = False

        line += ' '  # Force any macros at the end of the line to expand.

        for c in line:
            if c == '$' and not inMacro and parenLevel == 0:
                expanded += buff
                buff = ''
                inMacro = True
            elif c == '$' and parenLevel == 0 and inMacro and buff == '':
                inMacro = False
                expanded += '$'
            elif (c == '(' or c == '{') and inMacro:
                parenLevel += 1

                if parenLevel > 1:
                    buff += c
            elif (c == ')' or c == '}') and inMacro:
                parenLevel -= 1

                if parenLevel == 0:
                    inMacro = False
                    buffFromMacro = True
                else:
                    buff += c
            elif inMacro and parenLevel == 0 and \
                    not MACRO_NAME_CHAR_RE.match(c):
                inMacro = False
                buffFromMacro = True
                afterBuff += c
            else:
                buff += c

            if buffFromMacro:
                buffFromMacro = False
                buff = buff.lstrip()
                words = SPACE_CHARS.split(buff)

                if buff in macros:
                    buff = self.expandMacroUsages(macros[buff], macros)
                elif words[0] in self.macroCommands:
                    match = re.search("\s", buff)
                    argText = buff[match.end():]
                    buff = self.macroCommands[words[0]](argText, macros)
                elif self.expandUndefinedMacrosTo is None:
                    # If no default macro value, display an error message.
                    self.errorLogger.reportError(
                        f"Undefined macro {buff}. Context: {line}.")
                else:
                    # If we continue, expand to nothing.
                    buff = self.expandUndefinedMacrosTo

                expanded += buff
                expanded += afterBuff
#               print("Expanded to %s." % (buff + afterBuff))
                buff = ''
                afterBuff = ''

        if parenLevel > 0:
            self.errorLogger.reportError(f"Unclosed parenthesis: {line}")

        # Append buff, but ignore trailing space.
        expanded += buff[:len(buff) - 1] + afterBuff
        return expanded

    # Expand and handle macro definitions
    # in [contents]. This includes removing end-of-line comments.
    def expandAndDefineMacros(self, contents, macros=None, makeUtil=None):
        if macros is None:
            macros = {}
        lines = self.getLines(contents)
        result = ''
        conditionalData = None
        definitionName = None
        definitionData = []

        for lineNumber, line in enumerate(lines):
            if makeUtil is not None:
                makeUtil.currentLine = lineNumber
            line = self.stripComments(line)
            exporting = self.isMacroExport(line)

            if conditionalData is not None:
                if self.isConditional(line):
                    if CONDITIONAL_START.match(line):
                        conditionalData['stack'].append(line)
                        conditionalData['endifWeight'].append(1)
                    # We ignore CONDITIONAL_ELSE unless it applies directly to
                    # THIS conditional.
                    elif CONDITIONAL_ELSE.match(line) and \
                            len(conditionalData['stack']) == \
                            conditionalData['endifWeight'][-1]:
                        elseText = CONDITIONAL_ELSE.match(line).group(1)
#                        print("Else: " + line)
                        # Move anything after 'else' onto the next line
                        # (conceptually). Permits else if...
                        line = line.strip()[len(elseText):].strip()

                        if not conditionalData['elseBranch']:
                            conditionalData['elseBranch'] = ''
                        else:
                            conditionalData['elseBranch'] += 'else\n'
                        # We can start building-up the else branch...
                        conditionalData['elseBranch'] += line + '\n'

                        # Is it an else if?
                        if CONDITIONAL_START.match(line):
                            # Treat it like an if.
                            conditionalData['stack'].append(line)
                            # The next endif removes two elements from the
                            # stack.
                            conditionalData['endifWeight'].append(
                                conditionalData['endifWeight'][-1] + 1)

                        continue
                    elif CONDITIONAL_STOP.match(line):
                        # print(str(len(conditionalData['stack'])) + ","
                        # + line
                        # + ",  wt:" + str(conditionalData['endifWeight'][-1]))

                        while conditionalData['endifWeight'][-1] > 1:
                            conditionalData['elseBranch'] += 'endif\n'
                            conditionalData['stack'].pop()
                            conditionalData['endifWeight'][-1] -= 1

                        conditionalData['endifWeight'].pop()

                        # The endif applied to a sub-if statement.
                        if len(conditionalData['stack']) > 1:
                            conditionalData['stack'].pop()
                            # print("   To if. stack len: " + str(len(conditionalData['stack'])))
                        else:
                            # print("  To else")

                            # Contents of the if statement.
                            ifConditional = conditionalData['stack'].pop()

                            elsePart = conditionalData['elseBranch'] or ''

                            chosenBranch = self.evaluateIf(
                                ifConditional, conditionalData['ifBranch'],
                                elsePart, macros)

                            # We have reached the end of the branch. Add a
                            # version to result.
                            expanded, macros = self.expandAndDefineMacros(
                                chosenBranch, macros)
                            result += expanded + '\n'

                            # We are done!
                            conditionalData = None
                            continue
                if conditionalData['elseBranch'] is not None:
                    conditionalData['elseBranch'] += line + '\n'
                else:
                    conditionalData['ifBranch'] += line + '\n'
                continue

            if definitionName is not None:
                if line.startswith("endef"):
                    name = definitionName.strip()
                    macros[name] = "\n".join(definitionData)
                    definitionName = None
                else:
                    definitionData.append(line)
                continue

            if line.startswith("define"):
                definitionName = line[len("define "):]
                definitionData = []
                continue

            # If either a macro export, or a setting a macro's value, without
            # an export...
            if self.isMacroDef(line) or exporting:
                if exporting:
                    line = line[len("export "):]

                parts = MACRO_SET_RE.split(line)
                name = parts[0]
                definedTo = line[len(name):]

                # Remove the first set character.
                definedTo = MACRO_SET_RE.sub("", definedTo, count=1)

                # E.g. :,+,? so we can do += or ?=
                defineType = MACRO_SET_RE.search(line).group(1)
                name = name.strip()

                doNotDefine = False
                concatWith = ''
                deferExpand = False

                # ?=, so only define if undefined.
                if defineType == '?' and name in macros:
                    doNotDefine = True
                elif defineType == '+' and name in macros:
                    concatWith = macros[name]
                elif defineType == '':
                    deferExpand = True

                # Depending on the operator, we might not want to define the
                # macro...
                if not doNotDefine:
                    if concatWith != "" and definedTo != "":
                        concatWith += " "
                    if not deferExpand:
                        macros[name] = concatWith + self.expandMacroUsages(
                            definedTo, macros).rstrip('\n')
                    else:
                        # print(f"Expansion deferred: {name} = {definedTo}")
                        macros[name] = concatWith + definedTo.rstrip('\n')

                if exporting:
                    os.environ[name] = macros[name]
            # print("%s defined to %s" % (name, macros[name]))
            elif self.conditionals and self.isConditional(line) and \
                    not self.shouldLazyEval(line):
                # Ref:
                # https://www.gnu.org/software/make/manual/html_node/Conditional-Syntax.html#Conditional-Syntax
                conditional = self.getConditional(line)

                # The conditional must, initially, be some if...
                if not CONDITIONAL_START.match(conditional):
                    self.errorLogger.reportError(
                        f"{conditional} without a leading if. "
                        f"Context: {line}. Buffer: {result}")

                conditionalData = {
                    'ifBranch': '',
                    'elseBranch': None,
                    'stack': [],
                    'endifWeight': [1]
                }
                conditionalData['stack'].append(line)
            elif self.isMacroInvoke(line) and not self.shouldLazyEval(line):
                result += self.expandMacroUsages(line, macros)
            else:
                result += line
            result += '\n'

        if conditionalData is not None:
            self.errorLogger.reportError(
                "Un-ending conditional (check your indentation -- leading tabs"
                f"can mess things up)! Conditional data: {conditionalData}.")

        return result, macros
