package auditkit.facts;

import com.tngtech.archunit.core.domain.JavaAccess;
import com.tngtech.archunit.core.domain.JavaAnnotation;
import com.tngtech.archunit.core.domain.JavaClass;
import com.tngtech.archunit.core.domain.JavaClasses;
import com.tngtech.archunit.core.domain.JavaCodeUnit;
import com.tngtech.archunit.core.domain.JavaFieldAccess;
import com.tngtech.archunit.core.domain.JavaMethodCall;
import com.tngtech.archunit.core.importer.ClassFileImporter;
import com.tngtech.archunit.core.importer.ImportOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.TreeSet;

/**
 * What the compiled classes of the application say: for every method, the methods it calls and the
 * fields it reads or writes, with source line numbers; and which classes implement which interfaces.
 * Reflection and dynamic proxies are not visible here, which is why behavior is also observed at run time.
 */
public final class BytecodeFacts {

    private BytecodeFacts() {
    }

    public static Map<String, Object> collect(String basePackage) {
        JavaClasses classes = new ClassFileImporter()
                .withImportOption(ImportOption.Predefined.DO_NOT_INCLUDE_TESTS)
                .withImportOption(ImportOption.Predefined.DO_NOT_INCLUDE_JARS)
                .importPackages(basePackage);

        Map<String, Object> types = new TreeMap<String, Object>();
        for (JavaClass cls : classes) {
            if (cls.getName().startsWith("auditkit.")) {
                continue;
            }
            Map<String, Object> t = new LinkedHashMap<String, Object>();
            t.put("kind", cls.isInterface() ? "interface" : cls.isEnum() ? "enum" : "class");
            t.put("source", cls.getSource().isPresent() && cls.getSource().get().getFileName().isPresent()
                    ? cls.getSource().get().getFileName().get() : null);
            t.put("annotations", names(cls.getAnnotations()));
            TreeSet<String> supers = new TreeSet<String>();
            for (JavaClass i : cls.getAllRawInterfaces()) {
                supers.add(i.getName());
            }
            for (JavaClass s : cls.getAllRawSuperclasses()) {
                if (!s.getName().equals("java.lang.Object")) {
                    supers.add(s.getName());
                }
            }
            t.put("supertypes", new ArrayList<String>(supers));

            Map<String, Object> methods = new TreeMap<String, Object>();
            for (JavaCodeUnit unit : cls.getCodeUnits()) {
                Map<String, Object> m = new LinkedHashMap<String, Object>();
                m.put("annotations", names(unit.getAnnotations()));
                List<Map<String, Object>> calls = new ArrayList<Map<String, Object>>();
                for (JavaMethodCall call : unit.getMethodCallsFromSelf()) {
                    calls.add(access(call, call.getTargetOwner().getName(), call.getName(), null));
                }
                m.put("calls", calls);
                List<Map<String, Object>> fields = new ArrayList<Map<String, Object>>();
                for (JavaFieldAccess fa : unit.getFieldAccesses()) {
                    fields.add(access(fa, fa.getTargetOwner().getName(), fa.getName(),
                            fa.getAccessType() == JavaFieldAccess.AccessType.SET ? "set" : "get"));
                }
                m.put("fields", fields);
                methods.put(unit.getName() + descriptor(unit), m);
            }
            t.put("methods", methods);
            types.put(cls.getName(), t);
        }
        Map<String, Object> out = new LinkedHashMap<String, Object>();
        out.put("base_package", basePackage);
        out.put("types", types);
        return out;
    }

    private static Map<String, Object> access(JavaAccess<?> access, String owner, String name, String mode) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("owner", owner);
        m.put("name", name);
        if (mode != null) {
            m.put("mode", mode);
        }
        m.put("line", access.getLineNumber());
        return m;
    }

    private static String descriptor(JavaCodeUnit unit) {
        StringBuilder sb = new StringBuilder("(");
        List<JavaClass> params = unit.getRawParameterTypes();
        for (int i = 0; i < params.size(); i++) {
            sb.append(i == 0 ? "" : ",").append(params.get(i).getSimpleName());
        }
        return sb.append(")").toString();
    }

    private static List<String> names(Iterable<? extends JavaAnnotation<?>> annotations) {
        TreeSet<String> out = new TreeSet<String>();
        for (JavaAnnotation<?> a : annotations) {
            out.add(a.getRawType().getName());
        }
        return new ArrayList<String>(out);
    }
}
