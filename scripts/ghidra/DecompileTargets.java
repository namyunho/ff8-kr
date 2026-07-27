// Ghidra 헤드리스 postScript — 지정 주소의 함수를 디컴파일해 stdout으로 출력한다.
// IDA/idalib 결과와 독립 대조하기 위한 용도이며 프로그램 DB를 수정하지 않는다.
//
// 대상 주소는 환경변수 FF8_DECOMP_TARGETS 에 쉼표로 구분해 넣는다.
//   예: FF8_DECOMP_TARGETS=0x80035EC4,0x8002C358
//
// @category FF8

import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class DecompileTargets extends GhidraScript {

    @Override
    public void run() throws Exception {
        String spec = System.getenv("FF8_DECOMP_TARGETS");
        if (spec == null || spec.isBlank()) {
            println("FF8_DECOMP_TARGETS 가 비어 있다. 대상 주소를 지정한다.");
            return;
        }

        DecompInterface decompiler = new DecompInterface();
        if (!decompiler.openProgram(currentProgram)) {
            println("디컴파일러를 열지 못했다: " + decompiler.getLastMessage());
            return;
        }

        try {
            for (String token : spec.split(",")) {
                String text = token.trim();
                if (text.isEmpty()) {
                    continue;
                }
                decompileOne(decompiler, text);
            }
        }
        finally {
            decompiler.dispose();
        }
    }

    private void decompileOne(DecompInterface decompiler, String text) {
        Address address;
        try {
            address = toAddr(text);
        }
        catch (Exception e) {
            println("주소 해석 실패: " + text);
            return;
        }

        // 자동 분석이 함수를 못 만든 지점은 강제로 만들어 본다.
        Function function = getFunctionContaining(address);
        if (function == null) {
            function = createFunction(address, null);
        }
        if (function == null) {
            println("함수를 만들 수 없다: " + text);
            return;
        }

        println("======================================================================");
        println("=== " + function.getName() + " @ " + function.getEntryPoint()
                + "  (Ghidra " + currentProgram.getLanguageID() + ") ===");
        println("======================================================================");

        DecompileResults results = decompiler.decompileFunction(function, 120, monitor);
        if (results == null || !results.decompileCompleted()) {
            println("디컴파일 실패: "
                    + (results == null ? "결과 없음" : results.getErrorMessage()));
            return;
        }
        println(results.getDecompiledFunction().getC());
    }
}
