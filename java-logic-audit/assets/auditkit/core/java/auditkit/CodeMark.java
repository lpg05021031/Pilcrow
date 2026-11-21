package auditkit;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * A mark of the application as it was compiled for this run: a hash over the class and resource
 * directories on the class path, the test ones left out.
 *
 * <p>Two runs with the same mark ran the same application; a run with another mark is evidence about
 * other code. The scripts that judge use it to keep results from before a change apart from results
 * after it. Libraries (jar files) are not part of the mark: it is about the code being audited.
 */
public final class CodeMark {

    private CodeMark() {
    }

    /** The mark, or null when the class path holds no directory of the application's own. */
    public static String ofApplication() {
        try {
            List<File> roots = new ArrayList<File>();
            for (String entry : System.getProperty("java.class.path", "").split(File.pathSeparator)) {
                File f = new File(entry);
                if (f.isDirectory() && !isTestOutput(f.getAbsolutePath().replace('\\', '/'))) {
                    roots.add(f);
                }
            }
            if (roots.isEmpty()) {
                return null;
            }
            Collections.sort(roots);
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] buffer = new byte[8192];
            for (File root : roots) {
                add(md, root, "", buffer);
            }
            StringBuilder hex = new StringBuilder();
            for (byte b : md.digest()) {
                hex.append(String.format("%02x", b));
            }
            return hex.substring(0, 16);
        } catch (Exception e) {
            return null;        // a run without a mark is still a run
        }
    }

    /** Maven's target/test-classes, Gradle's build/classes/<language>/test and build/resources/test, and their variants. */
    static boolean isTestOutput(String path) {
        return path.endsWith("/test-classes") || path.matches(".*/(classes/[^/]+|resources)/(test|testFixtures|integrationTest|audit)$");
    }

    private static void add(MessageDigest md, File dir, String prefix, byte[] buffer) throws Exception {
        File[] children = dir.listFiles();
        if (children == null) {
            return;
        }
        Arrays.sort(children);
        for (File child : children) {
            String name = prefix + child.getName();
            if (child.isDirectory()) {
                add(md, child, name + "/", buffer);
            } else {
                md.update(name.getBytes("UTF-8"));
                md.update((byte) 0);
                InputStream in = new FileInputStream(child);
                try {
                    int n;
                    while ((n = in.read(buffer)) > 0) {
                        md.update(buffer, 0, n);
                    }
                } finally {
                    in.close();
                }
            }
        }
    }
}
