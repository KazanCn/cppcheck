#!/usr/bin/python
#
# Cppcheck - A tool for static C/C++ code analysis
# Copyright (C) 2007-2013 Daniel Marjamaeki and Cppcheck team.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import sys
import re
import glob
import argparse

class MatchCompiler:
    def __init__(self, verify_mode=False):
        self._verifyMode = verify_mode
        self._reset()
        self._selftests()

    def _reset(self):
        self._rawMatchFunctions = []
        self._matchStrs = {}
        self._matchFunctionCache = {}

    def _generateCacheSignature(self, pattern, endToken=None, varId=None, isFindMatch=False):
        sig = pattern

        if endToken:
            sig += '|ENDTOKEN'
        else:
            sig += '|NO-ENDTOKEN'

        if varId:
            sig += '|VARID'
        else:
            sig += '|NO-VARID'

        if isFindMatch:
            sig += '|ISFINDMATCH'
        else:
            sig += '|NORMALMATCH'

        return sig

    def _lookupMatchFunctionId(self, pattern, endToken=None, varId=None, isFindMatch=False):
        signature = self._generateCacheSignature(pattern, endToken, varId, isFindMatch)

        if signature in self._matchFunctionCache:
            return self._matchFunctionCache[signature]

        return None

    def _insertMatchFunctionId(self, id, pattern, endToken=None, varId=None, isFindMatch=False):
        signature = self._generateCacheSignature(pattern, endToken, varId, isFindMatch)

        # function signature should not be in the cache
        assert(self._lookupMatchFunctionId(pattern, endToken, varId, isFindMatch) == None)

        self._matchFunctionCache[signature] = id

    def _insertMatchStr(self, look_for):
        prefix = 'matchStr'

        # Add entry if needed
        if look_for not in self._matchStrs:
            pos = len(self._matchStrs) + 1
            self._matchStrs[look_for] = pos

        return prefix + str(self._matchStrs[look_for])

    def _compileCmd(self, tok):
        if tok == '%any%':
            return 'true'
        elif tok == '%bool%':
            return 'tok->isBoolean()'
        elif tok == '%char%':
            return '(tok->type()==Token::eChar)'
        elif tok == '%comp%':
            return 'tok->isComparisonOp()'
        elif tok == '%num%':
            return 'tok->isNumber()'
        elif tok == '%op%':
            return 'tok->isOp()'
        elif tok == '%or%':
            return '(tok->str()==' + self._insertMatchStr('|') + ')/* | */'
        elif tok == '%oror%':
            return '(tok->str()==' + self._insertMatchStr('||') + ')/* || */'
        elif tok == '%str%':
            return '(tok->type()==Token::eString)'
        elif tok == '%type%':
            return '(tok->isName() && tok->varId()==0U && tok->str() != ' + self._insertMatchStr('delete') + '/* delete */)'
        elif tok == '%var%':
            return 'tok->isName()'
        elif tok == '%varid%':
            return '(tok->isName() && tok->varId()==varid)'
        elif (len(tok)>2) and (tok[0]=="%"):
            print ("unhandled:" + tok)

        return '(tok->str()==' + self._insertMatchStr(tok) + ')/* ' + tok + ' */'

    def _compilePattern(self, pattern, nr, varid, isFindMatch=False):
        ret = ''
        returnStatement = ''

        if isFindMatch:
            ret = '\nconst Token *tok = start_tok;\n'
            returnStatement = 'continue;\n'
        else:
            arg2 = ''
            if varid:
                arg2 = ', const unsigned int varid'

            ret = '// pattern: ' + pattern + '\n'
            ret += 'static bool match' + str(nr) + '(const Token *tok'+arg2+') {\n'
            returnStatement = 'return false;\n'

        tokens = pattern.split(' ')
        gotoNextToken = ''
        checked_varid = False
        for tok in tokens:
            if tok == '':
                continue
            ret += gotoNextToken
            gotoNextToken = '    tok = tok->next();\n'

            # if varid is provided, check that it's non-zero on first use
            if varid and tok.find('%varid%') != -1 and checked_varid == False:
                ret += '    if (varid==0U)\n'
                ret += '        throw InternalError(tok, "Internal error. Token::Match called with varid 0. Please report this to Cppcheck developers");\n'
                checked_varid = True

            # [abc]
            if (len(tok) > 2) and (tok[0] == '[') and (tok[-1] == ']'):
                ret += '    if (!tok || tok->str().size()!=1U || !strchr("'+tok[1:-1]+'", tok->str()[0]))\n'
                ret += '        ' + returnStatement

            # a|b|c
            elif tok.find('|') > 0:
                tokens2 = tok.split('|')
                logicalOp = None
                neg = None
                if "" in tokens2:
                    ret += '    if (tok && ('
                    logicalOp = ' || '
                    neg = ''
                else:
                    ret += '    if (!tok || !('
                    logicalOp = ' || '
                    neg = ''
                first = True
                for tok2 in tokens2:
                    if tok2 == '':
                        continue
                    if not first:
                        ret += logicalOp
                    first = False
                    ret += neg + self._compileCmd(tok2)

                if "" in tokens2:
                    ret += '))\n'
                    ret += '        tok = tok->next();\n'
                    gotoNextToken = ''
                else:
                    ret += '))\n'
                    ret += '        ' + returnStatement

            # !!a
            elif tok[0:2] == "!!":
                ret += '    if (tok && tok->str() == ' + self._insertMatchStr(tok[2:]) + ')/* ' + tok[2:] + ' */\n'
                ret += '        ' + returnStatement
                gotoNextToken = '    tok = tok ? tok->next() : NULL;\n'

            else:
                ret += '    if (!tok || !' + self._compileCmd(tok) + ')\n'
                ret += '        ' + returnStatement

        if isFindMatch:
            ret += '    return start_tok;\n'
        else:
            ret += '    return true;\n'
            ret += '}\n'

        return ret

    def _compileFindPattern(self, pattern, findmatchnr, endToken, varId):
        more_args = ''
        endCondition = ''
        if endToken:
            more_args += ', const Token *end'
            endCondition = ' && start_tok != end'
        if varId:
            more_args += ', unsigned int varid'

        ret = '// pattern: ' + pattern + '\n'
        ret += 'static const Token *findmatch' + str(findmatchnr) + '(const Token *start_tok'+more_args+') {\n'
        ret += '    for (; start_tok' + endCondition + '; start_tok = start_tok->next()) {\n'

        ret += self._compilePattern(pattern, -1, varId, True)
        ret += '    }\n'
        ret += '    return NULL;\n}\n'

        return ret

    def parseMatch(self, line, pos1):
        parlevel = 0
        args = []
        argstart = 0
        pos = pos1
        inString = False
        while pos < len(line):
            if inString:
                if line[pos] == '\\':
                    pos += 1
                elif line[pos] == '"':
                    inString = False
            elif line[pos] == '"':
                inString = True
            elif line[pos] == '(':
                parlevel += 1
                if parlevel == 1:
                    argstart = pos + 1
            elif line[pos] == ')':
                parlevel -= 1
                if parlevel == 0:
                    ret = []
                    ret.append(line[pos1:pos+1])
                    for arg in args:
                        ret.append(arg)
                    ret.append(line[argstart:pos])
                    return ret
            elif line[pos] == ',' and parlevel == 1:
                args.append(line[argstart:pos])
                argstart = pos + 1
            pos += 1

        return None

    def _parseStringComparison(self, line, pos1):
        startPos = 0
        endPos = 0
        pos = pos1
        inString = False
        while pos < len(line):
            if inString:
                if line[pos] == '\\':
                    pos += 1
                elif line[pos] == '"':
                    inString = False
                    endPos = pos+1
                    return (startPos, endPos)
            elif line[pos] == '"':
                startPos = pos
                inString = True
            pos += 1

        return None

    def _compileVerifyTokenMatch(self, is_simplematch, verifyNumber, pattern, patternNumber, varId):
        more_args = ''
        if varId:
            more_args = ', const unsigned int varid'

        ret = 'static bool match_verify' + str(verifyNumber) + '(const Token *tok'+more_args+') {\n'

        origMatchName = 'Match'
        if is_simplematch:
            origMatchName = 'simpleMatch'
            assert(varId == None)

        ret += '    bool res_compiled_match = match'+str(patternNumber)+'(tok'
        if varId:
            ret += ', varid'
        ret += ');\n'

        ret += '    bool res_parsed_match = Token::' + origMatchName + '(tok, "' + pattern + '"'
        if varId:
            ret += ', varid'
        ret += ');\n'

        ret += '\n'
        # Don't use assert() here, it's disabled for optimized builds.
        # We also need to verify builds in 'release' mode
        ret += '    if (res_parsed_match != res_compiled_match) {\n'
#        ret += '        std::cout << "res_parsed_match' + str(verifyNumber) + ': " << res_parsed_match << ", res_compiled_match: " << res_compiled_match << "\\n";\n'
#        ret += '        if (tok)\n'
#        ret += '            std::cout << "tok: " << tok->str();\n'
#        ret += '        if (tok->next())\n'
#        ret += '            std::cout << "tok next: " << tok->next()->str();\n'
        ret += '        throw InternalError(tok, "Internal error. compiled match returned different result than parsed match");\n'
        ret += '    }\n'
        ret += '    return res_compiled_match;\n'
        ret += '}\n'

        return ret

    def _replaceSpecificTokenMatch(self, is_simplematch, line, start_pos, end_pos, pattern, tok, varId):
        more_args = ''
        if varId:
            more_args = ',' + varId

        # Compile function or use previously compiled one
        patternNumber = self._lookupMatchFunctionId(pattern, None, varId, False)

        if patternNumber == None:
            patternNumber = len(self._rawMatchFunctions) + 1
            self._insertMatchFunctionId(patternNumber, pattern, None, varId, False)
            self._rawMatchFunctions.append(self._compilePattern(pattern, patternNumber, varId))

        functionName = "match"
        if self._verifyMode:
            verifyNumber = len(self._rawMatchFunctions) + 1
            self._rawMatchFunctions.append(self._compileVerifyTokenMatch(is_simplematch, verifyNumber, pattern, patternNumber, varId))

            # inject verify function
            functionName = "match_verify"
            patternNumber = verifyNumber

        return line[:start_pos]+functionName+str(patternNumber)+'('+tok+more_args+')'+line[start_pos+end_pos:]

    def _replaceTokenMatch(self, line):
        while True:
            is_simplematch = False
            pos1 = line.find('Token::Match(')
            if pos1 == -1:
                is_simplematch = True
                pos1 = line.find('Token::simpleMatch(')
            if pos1 == -1:
                break

            res = self.parseMatch(line, pos1)
            if res == None:
                break

            assert(len(res)==3 or len(res)==4)  # assert that Token::Match has either 2 or 3 arguments

            end_pos = len(res[0])
            tok = res[1]
            raw_pattern = res[2]
            varId = None
            if len(res) == 4:
                varId = res[3]

            res = re.match(r'\s*"([^"]*)"\s*$', raw_pattern)
            if res == None:
                break  # Non-const pattern - bailout

            pattern = res.group(1)
            line = self._replaceSpecificTokenMatch(is_simplematch, line, pos1, end_pos, pattern, tok, varId)

        return line

    def _replaceTokenFindMatch(self, line):
        pos1 = 0
        while True:
            is_findmatch = False
            pos1 = line.find('Token::findsimplematch(')
            if pos1 == -1:
                is_findmatch = True
                pos1 = line.find('Token::findmatch(')
            if pos1 == -1:
                break

            res = self.parseMatch(line, pos1)
            if res == None:
                break
            else:
                assert(len(res)>=3 or len(res) < 6)  # assert that Token::find(simple)match has either 2, 3 or four arguments

                g0 = res[0]
                arg1 = res[1]
                pattern = res[2]

                # Check for varId
                varId = None
                if is_findmatch and g0.find("%varid%") != -1:
                    if len(res) == 5:
                        varId = res[4]
                    else:
                        varId = res[3]

                # endToken support. We resolve the overloaded type by checking if varId is used or not.
                # Function protoypes:
                #     Token *findsimplematch(const Token *tok, const char pattern[]);
                #     Token *findsimplematch(const Token *tok, const char pattern[], const Token *end);
                #     Token *findmatch(const Token *tok, const char pattern[], unsigned int varId = 0);
                #     Token *findmatch(const Token *tok, const char pattern[], const Token *end, unsigned int varId = 0);
                endToken = None
                if is_findmatch == False and len(res) == 4:
                    endToken = res[3]
                elif is_findmatch == True:
                    if varId and len(res) == 5:
                        endToken = res[3]
                    elif varId == None and len(res) == 4:
                        endToken = res[3]

                res = re.match(r'\s*"([^"]*)"\s*$', pattern)
                if res == None:
                    break  # Non-const pattern - bailout
                else:
                    pattern = res.group(1)
                    a3 = ''
                    if endToken:
                        a3 += ',' + endToken
                    if varId:
                        a3 += ',' + varId

                    # Compile function or use previously compiled one
                    findMatchNumber = self._lookupMatchFunctionId(pattern, endToken, varId, True)

                    if findMatchNumber == None:
                        findMatchNumber = len(self._rawMatchFunctions) + 1
                        self._insertMatchFunctionId(findMatchNumber, pattern, endToken, varId, True)
                        self._rawMatchFunctions.append(self._compileFindPattern(pattern, findMatchNumber, endToken, varId))

                    line = line[:pos1]+'findmatch'+str(findMatchNumber)+'('+arg1+a3+')'+line[pos1+len(g0):]

        return line

    def _replaceCStrings(self, line):
        while True:
            match = re.search('str\(\) (==|!=) "', line)
            if not match:
                match = re.search('strAt\(.+?\) (==|!=) "', line)
            if not match:
                break

            res = self._parseStringComparison(line, match.start())
            if res == None:
                break

            startPos = res[0]
            endPos = res[1]
            text = line[startPos+1:endPos-1]
            line = line[:startPos] + self._insertMatchStr(text) + line[endPos:]

        return line

    def convertFile(self, srcname, destname):
        self._reset()

        fin = open(srcname, "rt")
        srclines = fin.readlines()
        fin.close()

        header = '#include "token.h"\n'
        header += '#include "errorlogger.h"\n'
        header += '#include <string>\n'
        header += '#include <cstring>\n'
        # header += '#include <iostream>\n'
        code = ''

        for line in srclines:
            # Compile Token::Match and Token::simpleMatch
            line = self._replaceTokenMatch(line)

            # Compile Token::findsimplematch
            # NOTE: Not enabled for now since the generated code is slower than before.
            # line = self._replaceTokenFindMatch(line)

            # Cache plain C-strings in C++ strings
            line = self._replaceCStrings(line)

            code += line

        # Compute string list
        stringList = ''
        for match in sorted(self._matchStrs, key=self._matchStrs.get):
            stringList += 'static const std::string matchStr' + str(self._matchStrs[match]) + '("' + match + '");\n'

        # Compute matchFunctions
        strFunctions = ''
        for function in self._rawMatchFunctions:
            strFunctions += function

        fout = open(destname, 'wt')
        fout.write(header+stringList+strFunctions+code)
        fout.close()

    def _assertEquals(self, actual, expected):
        if actual != expected:
            print ('Assertion failed:')
            print ('"' + actual + '"')
            print ('"' + expected + '"')
            assert actual == expected

    def _selftest_parseMatch(self):
        self._assertEquals(self.parseMatch('  Token::Match(tok, ";") ',2), ['Token::Match(tok, ";")','tok',' ";"'])
        self._assertEquals(self.parseMatch('  Token::Match(tok,', 2), None) # multiline Token::Match is not supported yet
        self._assertEquals(self.parseMatch('  Token::Match(Token::findsimplematch(tok,")"), ";")', 2), ['Token::Match(Token::findsimplematch(tok,")"), ";")', 'Token::findsimplematch(tok,")")', ' ";"']) # inner function call

    def _selftest_replaceTokenMatch(self):
        self._reset()

        input = 'if (Token::Match(tok, "foobar")) {'
        output = self._replaceTokenMatch(input)
        self._assertEquals(output, 'if (match1(tok)) {')
        self._assertEquals(1, len(self._matchStrs))
        self._assertEquals(1, self._matchStrs['foobar'])

        input = 'if (Token::Match(tok->next()->next(), "foobar %type% %num%")) {'
        output = self._replaceTokenMatch(input)
        self._assertEquals(output, 'if (match2(tok->next()->next())) {')
        self._assertEquals(2, len(self._matchStrs))

        input = 'if (Token::Match(tok, "foo\"special\"bar %num%")) {'
        output = self._replaceTokenMatch(input)
        # FIXME: Currently detected as non-static pattern
        self._assertEquals(output, 'if (Token::Match(tok, "foo"special"bar %num%")) {')
        # self._assertEquals(3, len(self._matchStrs))

    def _selftest_replaceTokenMatchWithVarId(self):
        self._reset()

        input = 'if (Token::Match(tok, "foobar %varid%", 123)) {'
        output = self._replaceTokenMatch(input)
        self._assertEquals(output, 'if (match1(tok, 123)) {')
        self._assertEquals(1, len(self._matchStrs))

        input = 'if (Token::Match(tok->next()->next(), "%varid% foobar", tok->varId())) {'
        output = self._replaceTokenMatch(input)
        self._assertEquals(output, 'if (match2(tok->next()->next(), tok->varId())) {')
        self._assertEquals(1, len(self._matchStrs))

        input = 'if (Token::Match(tok, "foo\"special\"bar %type% %varid%", my_varid_cache)) {'
        output = self._replaceTokenMatch(input)
        # FIXME: Currently detected as non-static pattern
        self._assertEquals(output, 'if (Token::Match(tok, "foo"special"bar %type% %varid%", my_varid_cache)) {')
        # self._assertEquals(1, len(self._matchStrs))

        # test caching: reuse existing matchX()
        input = 'if (Token::Match(tok, "foobar %varid%", 123)) {'
        output = self._replaceTokenMatch(input)
        self._assertEquals(output, 'if (match1(tok, 123)) {')
        self._assertEquals(1, len(self._matchStrs))

        # two in one line
        input = 'if (Token::Match(tok, "foobar2 %varid%", 123) || Token::Match(tok, "%type% %varid%", 123)) {'
        output = self._replaceTokenMatch(input)
        self._assertEquals(output, 'if (match3(tok, 123) || match4(tok, 123)) {')
        self._assertEquals(3, len(self._matchStrs))

    def _selftest_replaceTokenSimpleMatch(self):
        self._reset()

        input = 'if (Token::simpleMatch(tok, "foobar")) {'
        output = self._replaceTokenMatch(input)
        self._assertEquals(output, 'if (match1(tok)) {')
        self._assertEquals(1, len(self._matchStrs))
        self._assertEquals(1, self._matchStrs['foobar'])

        input = 'if (Token::simpleMatch(tok->next()->next(), "foobar")) {'
        output = self._replaceTokenMatch(input)
        self._assertEquals(output, 'if (match1(tok->next()->next())) {')
        self._assertEquals(1, len(self._matchStrs))
        self._assertEquals(1, self._matchStrs['foobar'])

        input = 'if (Token::simpleMatch(tok, "foo\"special\"bar")) {'
        output = self._replaceTokenMatch(input)
        # FIXME: Currently detected as non-static pattern
        self._assertEquals(output, 'if (Token::simpleMatch(tok, "foo\"special\"bar")) {')
        self._assertEquals(1, len(self._matchStrs))

    def _selftest_replaceTokenFindSimpleMatch(self):
        self._reset()

        input = 'if (Token::findsimplematch(tok, "foobar")) {'
        output = self._replaceTokenFindMatch(input)
        self._assertEquals(output, 'if (findmatch1(tok)) {')
        self._assertEquals(1, len(self._matchStrs))
        self._assertEquals(1, self._matchStrs['foobar'])

        input = 'if (Token::findsimplematch(tok->next()->next(), "foobar", tok->link())) {'
        output = self._replaceTokenFindMatch(input)
        self._assertEquals(output, 'if (findmatch2(tok->next()->next(), tok->link())) {')
        self._assertEquals(1, len(self._matchStrs))
        self._assertEquals(1, self._matchStrs['foobar'])

        input = 'if (Token::findsimplematch(tok, "foo\"special\"bar")) {'
        output = self._replaceTokenFindMatch(input)
        # FIXME: Currently detected as non-static pattern
        self._assertEquals(output, 'if (Token::findsimplematch(tok, "foo\"special\"bar")) {')
        self._assertEquals(1, len(self._matchStrs))

    def _selftest_replaceTokenFindMatch(self):
        self._reset()

        input = 'if (Token::findmatch(tok, "foobar")) {'
        output = self._replaceTokenFindMatch(input)
        self._assertEquals(output, 'if (findmatch1(tok)) {')
        self._assertEquals(1, len(self._matchStrs))
        self._assertEquals(1, self._matchStrs['foobar'])

        # findmatch with varid
        input = 'if (Token::findmatch(tok, "foobar %varid%", tok->varId())) {'
        output = self._replaceTokenFindMatch(input)
        self._assertEquals(output, 'if (findmatch2(tok, tok->varId())) {')
        self._assertEquals(1, len(self._matchStrs))
        self._assertEquals(1, self._matchStrs['foobar'])

        # findmatch with end token
        input = 'if (Token::findmatch(tok->next()->next(), "foobar %type%", tok->link())) {'
        output = self._replaceTokenFindMatch(input)
        self._assertEquals(output, 'if (findmatch3(tok->next()->next(), tok->link())) {')
        self._assertEquals(2, len(self._matchStrs))
        self._assertEquals(1, self._matchStrs['foobar'])

        # findmatch with end token and varid
        input = 'if (Token::findmatch(tok->next()->next(), "foobar %type% %varid%", tok->link(), 123)) {'
        output = self._replaceTokenFindMatch(input)
        self._assertEquals(output, 'if (findmatch4(tok->next()->next(), tok->link(), 123)) {')
        self._assertEquals(2, len(self._matchStrs))
        self._assertEquals(1, self._matchStrs['foobar'])

    def _selftests(self):
        self._selftest_parseMatch()

        # Self test currently not compatible with the verify mode
        # as it modifies the expected output
        if self._verifyMode == False:
            self._selftest_replaceTokenMatch()
            self._selftest_replaceTokenMatchWithVarId()
            self._selftest_replaceTokenSimpleMatch()

            self._selftest_replaceTokenFindSimpleMatch()
            self._selftest_replaceTokenFindMatch()

        self._reset()

def main():
    # Main program
    build_dir = 'build'

    # Check if we are invoked from the right place
    if not os.path.exists('lib') and not os.path.exists('samples'):
        print('Please invoke from the top level cppcheck source dir. Example: tools/matchcompiler.py')
        sys.exit(-1)

    # Create build directory if needed
    if not os.path.exists(build_dir):
        os.makedirs(build_dir)
    if not os.path.isdir(build_dir):
        raise Exception(build_dir + ' is not a directory')

    # Argument handling
    parser = argparse.ArgumentParser(description='Compile Token::Match() calls into native C++ code')
    parser.add_argument('--verify', action='store_true', default=False,
                    help='verify compiled matches against on-the-fly parser. Slow!')
    args = parser.parse_args()

    mc = MatchCompiler(verify_mode=args.verify)

    # convert all lib/*.cpp files
    for f in glob.glob('lib/*.cpp'):
        print (f + ' => ' + build_dir + '/' + f[4:])
        mc.convertFile(f, build_dir + '/'+f[4:])

if __name__ == '__main__':
    main()
